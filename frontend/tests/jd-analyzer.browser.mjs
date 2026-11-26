import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { Agent, get } from 'node:http'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve, sep } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

const frontend = fileURLToPath(new URL('../', import.meta.url))
const backend = resolve(frontend, '../backend')
const api = 'http://127.0.0.1:8054'
const frontendUrl = 'http://localhost:5194'
const localAgent = new Agent({ proxyEnv: {} })
function localGet(url, headers) {
  return new Promise((resolveResponse, reject) => {
    get(url, { agent: localAgent, headers, timeout: 2000 }, (response) => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', (chunk) => { body += chunk })
      response.on('end', () => resolveResponse({ status: response.statusCode, body }))
    }).on('error', reject).on('timeout', function timeout() { this.destroy(new Error(`Timed out connecting to ${url}`)) })
  })
}
const ready = (url) => localGet(url).then(({ status }) => status === 200).catch(() => false)
const tempRoot = resolve(tmpdir())
const profile = await mkdtemp(resolve(tempRoot, 'jd-analyzer-edge-'))
const token = 'browser-test-token-not-a-production-secret-123456'
const edgePath = process.env.EDGE_PATH ?? resolve(
  process.env['ProgramFiles(x86)'] ?? 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe',
)

const backendProgram = `
import os
import pymupdf
from docx import Document
from pathlib import Path
from sys import stdin
from threading import Thread
from uvicorn import Config, Server
from app import resume_optimization
from app.main import app
from app.match_models import GeminiOutput

def mock_gemini(data):
    return GeminiOutput(suggestions=[{
        'candidate_id': candidate['candidate_id'],
        'suggested_replacement': candidate['suggested_replacement'],
    } for candidate in data['candidates']])

with pymupdf.open() as source:
    page = source.new_page()
    for y, line in [(50, 'Casey Sample'), (80, 'Skills'), (105, 'JS, Python'),
                    (140, 'Projects'), (165, 'Utilized HTML5 to build forms.')]:
        page.insert_text((72, y), line)
    path = Path(os.environ['BROWSER_FIXTURE_DIRECTORY']) / 'synthetic.pdf'
    if not path.exists():
        source.save(path)
docx_path = path.with_suffix('.docx')
if not docx_path.exists():
    document = Document()
    document.add_heading('Taylor Sample', 0)
    document.add_heading('Skills', 1)
    document.add_paragraph('TypeScript, Python')
    document.save(docx_path)
resume_optimization.request_gemini = mock_gemini
server = Server(Config(app, host='127.0.0.1', port=8054, access_log=False))
worker = Thread(target=server.run, daemon=True)
worker.start()
stdin.readline()
server.should_exit = True
worker.join(timeout=5)
`

let startupError
function startBackend() {
  const processHandle = spawn(resolve(backend, '.venv/Scripts/python.exe'), ['-u', '-c', backendProgram], {
    cwd: backend, stdio: ['pipe', 'inherit', 'inherit'], windowsHide: true,
    env: {
      ...process.env, RESUME_ENV: 'development', RESUME_WORKSPACE_TOKEN: token,
      FRONTEND_ORIGINS: frontendUrl, BROWSER_FIXTURE_DIRECTORY: profile, RESUME_DATA_DIR: resolve(profile, 'private-data'),
    },
  })
  processHandle.on('error', (error) => { startupError = error })
  processHandle.stdin.on('error', () => {})
  return processHandle
}

let backendProcess = startBackend()
const vite = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--port', '5194'], {
  cwd: frontend, stdio: 'inherit', windowsHide: true,
  env: { ...process.env, VITE_API_BASE_URL: api },
})
const edge = spawn(edgePath, [
  '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  '--no-first-run', '--disable-sync', '--disable-background-networking', 'about:blank',
], { stdio: 'ignore', windowsHide: true })
const closed = (child) => child.exitCode !== null || child.signalCode !== null
  ? Promise.resolve() : new Promise((done) => child.once('close', done))
for (const child of [vite, edge]) child.on('error', (error) => { startupError = error })

async function waitFor(check, label) {
  const deadline = Date.now() + 20000
  while (Date.now() < deadline) {
    if (startupError) throw startupError
    const result = await check()
    if (result) return result
    await delay(100)
  }
  throw new Error(`Timed out waiting for ${label}`)
}

let socket
let messageId = 0
const exceptions = []
function devtools(method, params = {}) {
  const id = ++messageId
  return new Promise((resolveMessage, reject) => {
    const timer = setTimeout(() => reject(new Error(`Browser command timed out: ${method}`)), 12000)
    function receive(event) {
      const data = JSON.parse(event.data)
      if (data.id !== id) return
      socket.removeEventListener('message', receive)
      clearTimeout(timer)
      if (data.error) reject(new Error(data.error.message))
      else resolveMessage(data.result)
    }
    socket.addEventListener('message', receive)
    socket.send(JSON.stringify({ id, method, params }))
  })
}

try {
  for (const url of [`${api}/api/health`, frontendUrl]) {
    await waitFor(() => ready(url), url)
  }
  const port = await waitFor(() => readFile(resolve(profile, 'DevToolsActivePort'), 'utf8')
    .then((value) => value.split(/\r?\n/)[0]).catch(() => false), 'Edge startup')
  const pages = JSON.parse((await localGet(`http://127.0.0.1:${port}/json/list`)).body)
  socket = new WebSocket(pages.find((page) => page.type === 'page').webSocketDebuggerUrl)
  await new Promise((done, reject) => {
    socket.addEventListener('open', done, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  socket.addEventListener('message', ({ data }) => {
    const event = JSON.parse(data)
    if (event.method === 'Runtime.exceptionThrown') exceptions.push(event.params.exceptionDetails)
  })
  await devtools('Runtime.enable')
  await devtools('Page.navigate', { url: frontendUrl })
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: 'document.querySelector("#workspace-token") !== null',
  })).result.value, 'workspace locked')
  assert.equal((await localGet(`${api}/api/matches/sources`)).status, 401)
  const unlock = async (value) => devtools('Runtime.evaluate', {
    expression: `(() => {
      const input = document.querySelector('#workspace-token');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.form.querySelector('button').click();
    })()`,
  })
  await unlock('wrong-token')
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: 'document.querySelector("[role=alert]")?.textContent.includes("token was not accepted")',
  })).result.value, 'invalid token message')
  await unlock(token)
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: 'document.querySelector("#career-title")?.checkVisibility() && document.querySelector("[data-edition-count]")?.textContent === "0"',
  })).result.value, 'dashboard with real empty library count')
  const initialDesk = await devtools('Runtime.evaluate', {
    expression: `(() => {
      const rail = document.querySelector('.desktop-sidebar')
      const toggle = document.querySelector('.desktop-collapse')
      const compact = rail.offsetWidth <= 80 && toggle.getAttribute('aria-expanded') === 'false'
      toggle.click()
      return compact && !!document.querySelector('.folio-object[href="#active-master"]')
    })()`,
  })
  assert.equal(initialDesk.result.value, true, 'Desk starts with compact navigation and a working source intake')
  await waitFor(async () => (await devtools('Runtime.evaluate', { expression: "document.querySelector('.desktop-collapse').getAttribute('aria-expanded') === 'true'" })).result.value, 'Expand navigation rail')
  await devtools('Runtime.evaluate', { expression: "document.querySelector('.desktop-collapse').click()" })
  const theme = (await devtools('Runtime.evaluate', {
    expression: `(() => {
      const before = document.documentElement.dataset.theme;
      document.querySelector('.topbar [aria-label*="theme"]').click();
      const after = document.documentElement.dataset.theme;
      const expectedBackground = after === 'dark' ? '#080d19' : '#f8fafc';
      return before !== after && after === localStorage.getItem('career-workspace-theme')
        && getComputedStyle(document.documentElement).getPropertyValue('--app-bg').trim() === expectedBackground ? after : null;
    })()`,
  })).result.value
  assert.ok(theme, 'Theme toggle persists the selected theme')
  await devtools('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
  await devtools('Runtime.evaluate', { expression: 'document.querySelector(".mobile-menu").click(); undefined' })
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: 'document.querySelector("#mobile-navigation").open && document.documentElement.scrollWidth <= innerWidth',
  })).result.value, 'mobile drawer fits viewport')
  await devtools('Runtime.evaluate', {
    expression: `document.querySelector('#mobile-navigation a[href="#active-master"]').click(); undefined`,
  })
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: 'document.querySelector("#active-master").checkVisibility() && !document.querySelector("#mobile-navigation").open',
  })).result.value, 'drawer navigates and closes')
  await devtools('Page.navigate', { url: `${frontendUrl}/#tailor` })
  await devtools('Emulation.clearDeviceMetricsOverride')
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: 'document.querySelector("textarea[name=job_description]")?.checkVisibility() && document.querySelector("#active-master")?.checkVisibility()',
  })).result.value, 'JD analyzer')
  const desktop = await devtools('Runtime.evaluate', {
    expression: 'document.querySelector("textarea").getBoundingClientRect().right <= innerWidth',
  })
  assert.equal(desktop.result.value, true, 'JD analyzer fits the desktop viewport')
  await devtools('Emulation.setDeviceMetricsOverride', {
    width: 768, height: 1024, deviceScaleFactor: 1, mobile: false,
  })
  const tablet = await devtools('Runtime.evaluate', {
    expression: 'document.documentElement.scrollWidth <= innerWidth',
  })
  assert.equal(tablet.result.value, true, 'Workspace fits the tablet viewport')
  await devtools('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 1, mobile: true,
  })
  await devtools('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: profile })
  const fixture = (await readFile(resolve(profile, 'synthetic.pdf'))).toString('base64')

  const result = await devtools('Runtime.evaluate', {
    awaitPromise: true,
    returnByValue: true,
    expression: `(async () => {
      const textarea = document.querySelector('textarea[name=job_description]');
      const form = textarea.form;
      const section = form.closest('section');
      const button = form.querySelector('button');
      const alert = form.querySelector('[role=alert]');
      const status = form.querySelector('[role=status]');
      const fill = (text) => {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
        setter.call(textarea, text);
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      };
      const expect = async (check, label) => {
        for (let attempt = 0; attempt < 75; attempt++) {
          if (check()) return;
          await new Promise((done) => setTimeout(done, 40));
        }
        throw new Error(label + ' (alert=' + alert.textContent + ', status=' + status.textContent + ')');
      };
      if (section.getBoundingClientRect().right > innerWidth || !document.body.textContent.includes('Upload Resume')) {
        throw new Error('Mobile layout overflow or resume form missing');
      }
      const upload = document.querySelector('input[type=file]');
      const uploadButton = (name) => [...upload.form.querySelectorAll('button')].find((item) => item.textContent === name);
      const transfer = new DataTransfer();
      const bytes = Uint8Array.from(atob(${JSON.stringify(fixture)}), (char) => char.charCodeAt(0));
      transfer.items.add(new File([bytes], 'Software Engineer.pdf', { type: 'application/pdf' }));
      upload.files = transfer.files;
      upload.dispatchEvent(new Event('change', { bubbles: true }));
      await expect(() => !uploadButton('Upload Resume').disabled, 'Resume file selected');
      uploadButton('Upload Resume').click();
      await expect(() => uploadButton('Extract Resume'), 'Resume upload');
      uploadButton('Extract Resume').click();
      await expect(() => upload.form.textContent.includes('Resume extracted.') &&
        upload.form.textContent.includes('JS, Python'), 'Resume extraction preview');
      const master = document.querySelector('#active-master');
      await expect(() => !master.querySelector('button').disabled, 'Extracted resume selectable as master');
      if (master.querySelector('select').selectedOptions[0].textContent !== 'Software Engineer') {
        throw new Error('Uploaded filename should label the master option');
      }
      master.querySelector('button').click();
      await expect(() => master.textContent.includes('Active master resume saved.'), 'Set active master');
      button.click();
      await expect(() => alert.textContent.includes('Paste a job description'), 'Empty input feedback');
      const originalFetch = window.fetch.bind(window);
      window.fetch = (...args) => new Promise((done) => setTimeout(done, 250)).then(() => originalFetch(...args));
      fill('Job title: Python Developer\\nRequired skills: Python, JS\\nPreferred skills: AWS, HTML\\nDocker is not required.');
      button.click();
      await expect(() => button.disabled && status.textContent.includes('Analyzing'), 'Loading state');
      await expect(() => status.textContent.includes('Analysis saved'), 'Analysis success');
      const group = (title) => [...section.querySelectorAll('h3')].find((item) => item.textContent === title).parentElement;
      if (!group('Job title').textContent.includes('Python Developer') || !group('Required skills').textContent.includes('JavaScript')
          || !group('Preferred skills').textContent.includes('AWS') || group('ATS keywords').textContent.includes('Docker')) {
        throw new Error('Incorrect rendered analysis');
      }
      const evidence = section.querySelector('details');
      evidence.querySelector('summary').click();
      if (!evidence.open || !evidence.textContent.includes('Docker is not required')) throw new Error('Evidence disclosure');
      window.fetch = async () => { throw new TypeError('Synthetic offline error'); };
      fill('Tools: Git');
      button.click();
      await expect(() => alert.textContent.includes('Could not reach'), 'Network error feedback');
      window.fetch = originalFetch;

      const matchSection = document.querySelector('#resume-matching');
      const matchStatus = matchSection.querySelector('[role=status]');
      const matchButton = (name) => [...matchSection.querySelectorAll('button')].find((item) => item.textContent === name);
      if (matchSection.getBoundingClientRect().right > innerWidth) throw new Error('Matching dashboard mobile overflow');
      await expect(() => matchButton('Refresh sources') && !matchButton('Refresh sources').disabled, 'Automatic source refresh finished');
      window.fetch = (...args) => new Promise((done) => setTimeout(done, 180)).then(() => originalFetch(...args));
      matchButton('Refresh sources').click();
      await expect(() => matchButton('Loading sources…')?.disabled, 'Manual source refresh starts');
      const selectors = [...matchSection.querySelectorAll('select')];
      await expect(() => selectors.every((select) => !select.disabled && select.options.length > 1), 'Saved sources');
      window.fetch = originalFetch;
      if (selectors[0].options[1].textContent !== 'Software Engineer') throw new Error('Match selector should show the uploaded resume name');
      for (const select of selectors) {
        select.value = select.options[1].value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
      await expect(() => !matchButton('Compare Resume with JD').disabled, 'Compare enabled for selected sources');
      matchButton('Compare Resume with JD').click();
      await expect(() => matchStatus.textContent.includes('Comparison saved'), 'Compare saved records');
      await expect(() => matchSection.textContent.includes('75%') && matchSection.textContent.includes('Internal keyword coverage'), 'Coverage table');
      const matchedNode = matchSection.querySelector('.node-row[data-state="Matched"] button')
      matchedNode.click()
      await expect(() => matchSection.querySelector('.node-evidence')?.textContent.includes('Resume'), 'Skill node opens source evidence')
      if (matchSection.querySelector('.node-row[data-state="Missing"] svg')) throw new Error('Missing skills must be unconnected')
      const resumeColumn = [...matchSection.querySelectorAll('h3')].find((item) => item.textContent === 'My Resume').parentElement;
      const jdColumn = [...matchSection.querySelectorAll('h3')].find((item) => item.textContent === 'Job Description').parentElement;
      if (jdColumn.getBoundingClientRect().top <= resumeColumn.getBoundingClientRect().top) throw new Error('Mobile comparison should stack');
      const gap = [...matchSection.querySelectorAll('p.font-semibold')].find((item) => item.textContent.startsWith('AWS ·')).parentElement;
      const findButton = (root, name) => [...root.querySelectorAll('button')].find((item) => item.textContent === name);
      const fillText = (input, value) => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      };
      findButton(gap, 'Not Experienced').click();
      await expect(() => gap.textContent.includes('Not experienced') && !findButton(gap, 'Review Later').disabled, 'Not experienced is saved');
      findButton(gap, 'Review Later').click();
      await expect(() => gap.textContent.includes('Review later') && !findButton(gap, 'Already Know This Skill').disabled, 'Review later is saved');
      findButton(gap, 'Already Know This Skill').click();
      await expect(() => gap.querySelector('textarea'), 'Evidence form opens');
      fillText(gap.querySelector('textarea'), 'Used AWS to host a personal demo.');
      await expect(() => !findButton(gap, 'Save Evidence').disabled, 'Evidence form accepts text');
      findButton(gap, 'Save Evidence').click();
      await expect(() => matchSection.textContent.includes('Self-reported evidence') &&
        !matchButton('Generate AI Improvements').disabled, 'Evidence became a pending proposal');
      matchButton('Generate AI Improvements').click();
      await expect(() => {
        const highlights = [...matchSection.querySelectorAll('article mark')].map((item) => item.textContent);
        return highlights.includes('JavaScript') && highlights.includes('HTML5');
      }, 'Verified keyword and alias highlighting');
      const verified = [...matchSection.querySelectorAll('article')].find((article) => article.textContent.includes('JavaScript'));
      findButton(verified, 'Edit').click();
      await expect(() => verified.querySelector('textarea'), 'Open manual editor');
      fillText(verified.querySelector('textarea'), 'Invented an AWS credential.');
      findButton(verified, 'Undo Draft').click();
      await expect(() => !verified.querySelector('textarea') && !verified.textContent.includes('Invented an AWS credential'), 'Undo unsaved draft');
      findButton(verified, 'Edit').click();
      await expect(() => verified.querySelector('textarea'), 'Reopen manual editor');
      fillText(verified.querySelector('textarea'), 'JavaScript, Python.');
      findButton(verified, 'Save Edit').click();
      await expect(() => !verified.querySelector('textarea') && verified.textContent.includes('JavaScript, Python.'), 'Verified manual wording saved');
      matchButton('Accept').click();
      await expect(() => matchButton('Accept').disabled && matchStatus.textContent.includes('Decision saved'), 'Accept suggestion');
      matchButton('Reject').click();
      await expect(() => matchButton('Reject').disabled && matchStatus.textContent.includes('Decision saved'), 'Reject suggestion');
      matchButton('Compare Resume with JD').click();
      await expect(() => matchStatus.textContent.includes('Comparison saved') && matchButton('Reject')?.disabled, 'Restore saved decision');
      const generator = [...document.querySelectorAll('h2')].find((item) => item.textContent === 'ATS Resume Generator').parentElement;
      const generate = generator.querySelector('button');
      const generationStatus = generator.querySelector('[role=status]');
      const generationError = generator.querySelector('[role=alert]');
      if (generator.getBoundingClientRect().right > innerWidth || !generator.textContent.includes('No suggestions approved')) {
        throw new Error('Generator layout or zero-approved state');
      }
      window.fetch = async () => { throw new TypeError('Synthetic offline error'); };
      generate.click();
      await expect(() => generationError.textContent.includes('offline'), 'Generation error');
      window.fetch = (...args) => new Promise((done) => setTimeout(done, 180)).then(() => originalFetch(...args));
      generate.click();
      await expect(() => generate.disabled && generationStatus.textContent.includes('Preparing'), 'Generation loading state');
      await expect(() => generationStatus.textContent.includes('1 page') && generationStatus.textContent.includes('0 applied edits'), 'Export with no approvals');
      window.fetch = originalFetch;

      matchButton('Accept').click();
      await expect(() => generator.isConnected === false, 'Refresh generator after approval');
      const currentGenerator = [...document.querySelectorAll('h2')].find((item) => item.textContent === 'ATS Resume Generator').parentElement;
      if (!currentGenerator.textContent.includes('1 approved suggestion')) throw new Error('Approved count');
      const approvedPreview = () => [...matchSection.querySelectorAll('summary')].find((item) => item.textContent === 'View approved resume content');
      await expect(() => approvedPreview(), 'Approved resume preview');
      approvedPreview().click();
      if (!approvedPreview().parentElement.textContent.includes('JavaScript, Python.') ||
          approvedPreview().parentElement.textContent.includes('Used AWS to host a personal demo.')) {
        throw new Error('Preview should include approved manual wording and exclude pending skill evidence');
      }
      currentGenerator.querySelector('button').click();
      await expect(() => currentGenerator.querySelector('[role=status]').textContent.includes('1 applied edit'), 'Export with approved edit');
      await expect(() => currentGenerator.querySelector('iframe'), 'Authenticated PDF preview');
      const readyPreview = currentGenerator.querySelector('iframe');
      const download = [...currentGenerator.querySelectorAll('a')].find((item) => item.textContent === 'Download PDF');
      if (readyPreview.src !== download.href || readyPreview.getBoundingClientRect().right > innerWidth) {
        throw new Error('Inline preview address or layout');
      }
      const previewResponse = await fetch(readyPreview.src);
      if (!previewResponse.ok || !(await previewResponse.text()).startsWith('%PDF-')) throw new Error('Invalid PDF preview');
      download.click();
      const layout = [...document.querySelectorAll('h2')].find((item) => item.textContent === 'Original-layout PDF').parentElement;
      layout.querySelector('button').click();
      await expect(() => layout.querySelector('iframe'), 'Original-layout preview: ' + layout.textContent);
      if (!layout.querySelector('[role=status]').textContent.includes('1 applied edit')) throw new Error('Original-layout applied count');
      const layoutDownload = layout.querySelector('a[download]');
      const layoutResponse = await fetch(layout.querySelector('iframe').src);
      if (!layoutResponse.ok || !(await layoutResponse.text()).startsWith('%PDF-')) throw new Error('Invalid layout preview');
      layoutDownload.click();
      return [download.download, layoutDownload.download];
    })()`,
  })
  assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails))
  await devtools('Emulation.setDeviceMetricsOverride', { width: 1024, height: 900, deviceScaleFactor: 1, mobile: false })
  const columns = await devtools('Runtime.evaluate', {
    expression: `(() => {
      const headings = [...document.querySelectorAll('#resume-matching h3')];
      const left = headings.find((heading) => heading.textContent === 'My Resume').parentElement.getBoundingClientRect();
      const right = headings.find((heading) => heading.textContent === 'Job Description').parentElement.getBoundingClientRect();
      return left.top === right.top && right.left >= left.right;
    })()`,
  })
  assert.equal(columns.result.value, true, 'Desktop comparison uses two columns')
  await devtools('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
  for (const filename of result.result.value) {
    assert.match(filename, /^(ats|layout)-resume-[a-f0-9-]{36}\.pdf$/)
    const downloaded = await waitFor(() => readFile(resolve(profile, filename)).catch(() => false), filename)
    assert.equal(downloaded.subarray(0, 5).toString(), '%PDF-')
  }
  async function reloadAndCheck(label) {
    const { result: { value: previous } } = await devtools('Runtime.evaluate', { expression: 'performance.timeOrigin' })
    await devtools('Page.reload')
    await waitFor(async () => (await devtools('Runtime.evaluate', {
      expression: `performance.timeOrigin !== ${previous} && document.documentElement.dataset.theme === ${JSON.stringify(theme)} && document.querySelectorAll("iframe[title*=resume], iframe[title*=original-layout]").length === 2 &&
        [...document.querySelectorAll("article button")].some((button) => button.disabled && button.textContent === "Accept") &&
        document.querySelector("#active-master select")?.selectedOptions[0]?.textContent === "Software Engineer" &&
        document.querySelector("#resume-matching").textContent.includes("JavaScript, Python.") &&
        document.querySelector("#resume-matching").textContent.includes("Used AWS to host a personal demo.")`,
    }).catch(() => ({ result: {} }))).result.value, label)
  }
  await reloadAndCheck('restore accepted suggestion and both exports on refresh')
  backendProcess.stdin.end('\n')
  await closed(backendProcess)
  assert.equal(backendProcess.exitCode, 0, 'First backend exited cleanly')
  backendProcess = startBackend()
  await waitFor(() => ready(`${api}/api/health`), 'restarted backend')
  await reloadAndCheck('restore accepted suggestion and both exports after backend restart')
  const history = await devtools('Runtime.evaluate', {
    awaitPromise: true,
    returnByValue: true,
    expression: `(async () => {
      const expect = async (check, name) => {
        for (let attempt = 0; attempt < 100; attempt++) {
          if (check()) return;
          await new Promise((done) => setTimeout(done, 40));
        }
        throw new Error(name);
      };
      const fill = (element, value) => {
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value').set.call(element, value);
        element.dispatchEvent(new Event('input', { bubbles: true }));
      };
      const button = (section, text) => [...section.querySelectorAll('button')].find((node) => node.textContent === text);
      const master = document.querySelector('#active-master');
      await expect(() => master.textContent.includes('Active resume · Software Engineer'), 'Active master filename restored after restart');
      const masterSelect = master.querySelector('select');
      if (masterSelect.selectedOptions[0].textContent !== 'Software Engineer') throw new Error('Master option name after restart');
      const resumeSelect = document.querySelector('#resume-matching select');
      if (resumeSelect.selectedOptions[0].textContent !== 'Software Engineer') throw new Error('Match option name after restart');
      button(master, 'Rename').click();
      await expect(() => master.querySelector('.source-preview-panel details input')?.checkVisibility(), 'Visible rename action opens file details');
      const resumeName = master.querySelector('details input');
      fill(resumeName, 'Software engineer resume');
      await expect(() => !resumeName.form.querySelector('button').disabled, 'Rename uploaded resume');
      resumeName.form.querySelector('button').click();
      await expect(() => masterSelect.selectedOptions[0].textContent === 'Software engineer resume' &&
        resumeSelect.selectedOptions[0].textContent === 'Software engineer resume', 'Custom name updates both selectors');
      const text = document.querySelector('textarea[name=job_description]');
      fill(text, 'Job title: Reuse check\\nRequired skills: Python, JavaScript');
      text.form.querySelector('button').click();
      const recommended = () => [...document.querySelectorAll('li')].find((row) => row.querySelector('p')?.textContent === 'Recommended');
      await expect(() => recommended(), 'Automatic highlighted recommendation after job analysis');
      const card = recommended();
      button(card, 'Use This Resume').click();
      await expect(() => card.closest('section').textContent.includes('Download started'), 'Use This Resume downloads for manual reuse');
      const preview = [...card.querySelectorAll('a')].find((link) => link.textContent === 'Preview Resume');
      const chosen = preview.hash.split('/')[1];
      preview.click();
      await expect(() => document.querySelector('#history-title') &&
        document.querySelector('iframe[title="Saved resume PDF preview"]'), 'Open recommended version from My Resumes');
      const page = document.querySelector('#history-title').closest('section');
      if (page.getBoundingClientRect().right > innerWidth) throw new Error('History page mobile layout overflow');
      if (!document.querySelector('nav a[href="#active-master"]')) throw new Error('Master resume navigation should remain available from the library');
      const version = page.querySelector('article');
      const download = version.querySelector('a[download]');
      const blob = await fetch(download.href);
      if (!(await blob.text()).startsWith('%PDF-')) throw new Error('Reuse preview should serve the saved PDF');
      download.download = 'preview-' + download.download;
      const previewFilename = download.download;
      download.click();
      fill(version.querySelector('input'), 'Shortlisted Developer PDF');
      await expect(() => !button(version, 'Save name').disabled, 'Rename enabled');
      button(version, 'Save name').click();
      await expect(() => version.querySelector('h3').textContent === 'Shortlisted Developer PDF', 'Saved custom name');
      button(version, 'Mark as favorite').click();
      await expect(() => button(version, 'Remove favorite'), 'Saved favorite');
      page.querySelector('input[type=checkbox]').click();
      await expect(() => page.querySelectorAll('li').length === 1 && page.querySelector('li').textContent.includes('Favorite'), 'Favorite filter');
      page.querySelector('input[type=checkbox]').click();
      await expect(() => page.querySelectorAll('li').length > 1, 'Clear favorite filter');
      fill(page.querySelector('input[type=search]'), 'Shortlisted Developer PDF');
      await expect(() => page.querySelectorAll('li').length === 1 && page.querySelector('li').textContent.includes('Shortlisted Developer PDF'), 'Custom name search');
      fill(page.querySelector('input[type=search]'), '');
      const [mode, sort] = page.querySelectorAll('select');
      mode.value = 'original_layout';
      mode.dispatchEvent(new Event('change', { bubbles: true }));
      await expect(() => page.querySelectorAll('li').length === 1 && page.querySelector('li').textContent.includes('Original-layout PDF'), 'Filter by export mode');
      mode.value = '';
      mode.dispatchEvent(new Event('change', { bubbles: true }));
      sort.value = 'name';
      sort.dispatchEvent(new Event('change', { bubbles: true }));
      await expect(() => [...page.querySelectorAll('li a.resume-card-title')].map((link) => link.textContent).join('|')
        === 'Shortlisted Developer PDF|Software Engineer.pdf|Software Engineer.pdf', 'Sort by displayed resume filename');
      await expect(() => page.querySelector('[aria-label="List view"]'), 'Library view controls');
      page.querySelector('.mini-stage').scrollIntoView({block: 'center'})
      await expect(() => page.querySelector('.mini-sheet canvas:not([hidden])'), 'Rasterize first page of a saved PDF')
      button(page, 'Carousel').click()
      await expect(() => page.querySelector('.library-carousel'), 'Open document carousel')
      button(page, 'Next resume').click()
      await expect(() => page.querySelector('.rail-controls span')?.textContent.startsWith('2 /'), 'Advance carousel')
      page.querySelector('.library-carousel').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))
      await expect(() => page.querySelector('.rail-controls span')?.textContent.startsWith('1 /'), 'Navigate carousel by keyboard')
      page.querySelector('[aria-label="List view"]').click();
      await expect(() => page.querySelector('.library-list'), 'Switch to library list view');
      location.hash = 'resume-history/' + chosen;
      await expect(() => document.querySelector('.version-comparison select')?.options.length > 1, 'Find earlier versions from the same source');
      const otherVersion = document.querySelector('.version-comparison select');
      otherVersion.selectedIndex = 1;
      otherVersion.dispatchEvent(new Event('change', { bubbles: true }));
      await expect(() => document.querySelector('.version-comparison')?.textContent.includes('identical section text')
        || document.querySelector('.version-comparison')?.textContent.includes('Comparison version'), 'Compare saved section snapshots');
      location.hash = 'my-resumes/' + chosen;
      await expect(() => page.querySelector('article')?.querySelector('h3').textContent === 'Shortlisted Developer PDF'
        && !page.querySelector('.version-comparison'), 'Return to selected library version');
      button(version, 'Delete version').click();
      await expect(() => button(version, 'Confirm deletion'), 'Delete confirmation');
      button(version, 'Cancel').click();
      await expect(() => !button(version, 'Confirm deletion') && version.querySelector('iframe'), 'Cancel leaves PDF available');
      button(version, 'Delete version').click();
      await expect(() => button(version, 'Confirm deletion'), 'Repeat delete confirmation');
      button(version, 'Confirm deletion').click();
      await expect(() => !page.querySelector('article') && page.querySelectorAll('li').length >= 1 &&
        !page.querySelector('a[href="#my-resumes/' + chosen + '"]'), 'Delete only selected version');
      location.hash = 'settings';
      await expect(() => document.querySelector('nav a[href="#tailor"]'), 'Combined workflow link in Settings');
      document.querySelector('nav a[href="#tailor"]').click();
      await expect(() => text.checkVisibility(), 'Return to tailoring');
      fill(text, 'Job title: Infrastructure Engineer\\nRequired skills: Terraform');
      text.form.querySelector('button').click();
      const useMaster = () => button(text.form.parentElement, 'Create New Optimized Resume');
      await expect(() => useMaster(), 'Fallback recommends the active master');
      useMaster().click();
      const panel = document.querySelector('#resume-matching');
      await expect(() => panel.querySelectorAll('select')[1].selectedOptions[0]?.textContent.includes('Infrastructure Engineer')
        && !button(panel, 'Compare Resume with JD').disabled, 'Fallback preselects master and JD');
      if (panel.querySelector('iframe')) throw new Error('Fallback should wait for a new comparison and manual export');
      return ['resume-' + chosen + '.pdf', previewFilename];
    })()`,
  })
  assert.equal(history.exceptionDetails, undefined, JSON.stringify(history.exceptionDetails))
  const downloadedVersions = await Promise.all(history.result.value.map((filename) =>
    waitFor(() => readFile(resolve(profile, filename)).catch(() => false), filename)))
  assert.equal(downloadedVersions[0].subarray(0, 5).toString(), '%PDF-')
  assert.deepEqual(downloadedVersions[0], downloadedVersions[1], 'Direct reuse and history download return the same PDF')
  const docxFixture = (await readFile(resolve(profile, 'synthetic.docx'))).toString('base64')
  const studio = await devtools('Runtime.evaluate', {
    awaitPromise: true, returnByValue: true,
    expression: `(async () => {
      const expect = async (check, message) => {
        for (let attempt = 0; attempt < 120; attempt++) {
          if (check()) return;
          await new Promise((done) => setTimeout(done, 40));
        }
        throw new Error(message);
      };
      location.hash = 'jd-analyzer';
      const jd = document.querySelector('textarea[name=job_description]');
      await expect(() => jd.checkVisibility(), 'Dedicated JD Analyzer');
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(jd, 'Job title: Studio check\\nRequired skills: Python, JavaScript');
      jd.dispatchEvent(new Event('input', { bubbles: true }));
      jd.form.querySelector('button').click();
      await expect(() => document.querySelector('.comparison-heading .primary-button')?.checkVisibility(), 'Automatic master comparison after analysis');
      document.querySelector('.comparison-heading a').click();
      await expect(() => document.querySelector('.studio-tabs')?.checkVisibility(), 'Studio mobile panels');
      [...document.querySelectorAll('.studio-tabs button')].find((item) => item.textContent === 'Preview').click();
      await expect(() => document.querySelector('.preview-paper')?.checkVisibility(), 'Open live preview tab');
      document.querySelector('.zoom-controls button[aria-label="Zoom in"]').click();
      await expect(() => document.querySelector('.zoom-controls output').textContent === '110%', 'Zoom live document text');
      location.hash = 'dashboard';
      await expect(() => document.querySelector('#career-title')?.checkVisibility()
        && document.querySelector('[data-edition-count]')?.textContent === '2'
        && document.querySelector('[data-best-match]')?.textContent === '100%',
        'Dashboard uses latest analyzed JD and current saved exports');
      document.querySelector('.folio-object').click()
      await expect(() => document.querySelector('.folio-dialog[open] article')?.textContent.includes('Python'), 'Read actual master resume on the desk')
      document.querySelector('.folio-dialog button').click()
      location.hash = 'active-master';
      await expect(() => document.querySelector('.upload-panel').checkVisibility(), 'Open the dedicated Master Resume page');
      document.querySelector('.source-preview-panel').open = true
      await expect(() => document.querySelector('.master-preview iframe')?.checkVisibility(), 'Preview stored original master PDF');
      const file = document.querySelector('.upload-panel input[type=file]');
      const transfer = new DataTransfer();
      const bytes = Uint8Array.from(atob(${JSON.stringify(docxFixture)}), (char) => char.charCodeAt(0));
      transfer.items.add(new File([bytes], 'Frontend Engineer.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }));
      file.files = transfer.files;
      file.dispatchEvent(new Event('change', { bubbles: true }));
      const uploadButton = (text) => [...file.form.querySelectorAll('button')].find((item) => item.textContent === text);
      await expect(() => !uploadButton('Upload Resume').disabled, 'Choose a DOCX file');
      uploadButton('Upload Resume').click();
      await expect(() => uploadButton('Extract Resume'), 'Upload DOCX');
      uploadButton('Extract Resume').click();
      await expect(() => file.form.textContent.includes('TypeScript, Python'), 'Extract and preview DOCX text');
      const sourceDownload = document.querySelector('.master-preview a[download]');
      const original = new Uint8Array(await (await fetch(sourceDownload.href)).arrayBuffer());
      if (sourceDownload.download !== 'Frontend Engineer.docx' || original.length !== bytes.length
        || !original.every((byte, index) => byte === bytes[index])) throw new Error('Download original DOCX');
    })()`,
  })
  assert.equal(studio.exceptionDetails, undefined, JSON.stringify(studio.exceptionDetails))
  const redesigned = await devtools('Runtime.evaluate', { awaitPromise: true, returnByValue: true,
    expression: `(async () => {
      const wait = async (check, message) => {
        for (let i = 0; i < 160; i++) { if (check()) return; await new Promise(done => setTimeout(done, 40)) }
        throw new Error(message)
      }
      const go = async route => {
        location.hash = route
        await wait(() => document.querySelector('.sidebar-nav a[aria-current="page"]')?.hash === '#' + route, 'Navigation to ' + route)
      }
      await go('jobs')
      const search = document.querySelector('.jd-filters input[type=search]')
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(search, 'Studio check')
      search.dispatchEvent(new Event('input', {bubbles:true}))
      await wait(() => document.querySelectorAll('#resume-matching .source-management li').length === 1, 'Job search')
      await go('match-studio')
      await wait(() => document.querySelector('.comparison-grid')?.checkVisibility(), 'Comparison studio')
      await go('export-center')
      const formats = document.querySelectorAll('.format-options input')
      formats[1].click()
      await wait(() => [...document.querySelectorAll('.export-panel')].some(panel => panel.checkVisibility() && panel.textContent.includes('Original-layout PDF')), 'Layout export selection')
      formats[0].click()
      await wait(() => [...document.querySelectorAll('.export-panel')].some(panel => panel.checkVisibility() && panel.textContent.includes('ATS Resume Generator')), 'ATS export selection')
      const ats = [...document.querySelectorAll('.export-panel')].find(panel => panel.checkVisibility())
      ats.querySelector('button').click()
      await wait(() => ats.querySelector('iframe')?.checkVisibility(), 'Export Center generates a readable PDF')
      await go('jobs')
      if (search.value !== 'Studio check') throw new Error('Saved job search lost on navigation')
      await go('active-master')
    })()` })
  assert.equal(redesigned.exceptionDetails, undefined, JSON.stringify(redesigned.exceptionDetails))
  const sourceDeletion = await devtools('Runtime.evaluate', {
    awaitPromise: true, returnByValue: true,
    expression: `(async () => {
      const expect = async (check, label) => {
        for (let attempt = 0; attempt < 120; attempt++) {
          if (check()) return
          await new Promise(done => setTimeout(done, 40))
        }
        throw new Error(label)
      }
      const click = (root, name) => [...root.querySelectorAll('button')].find(button =>
        (button.getAttribute('aria-label') ?? button.textContent) === name).click()
      const uploads = document.querySelector('.master-panel .source-management')
      const modal = () => document.querySelector('.confirm-dialog[open]')
      uploads.open = true
      click(uploads, 'Delete Frontend Engineer.docx')
      await expect(modal, 'Open deletion confirmation')
      if (!modal().textContent.includes('Frontend Engineer.docx')) throw new Error('Confirmation names the original file')
      click(modal(), 'Cancel')
      await expect(() => !modal(), 'Close deletion confirmation on cancel')
      if (!uploads.textContent.includes('Frontend Engineer.docx')) throw new Error('Cancel keeps the upload')
      click(uploads, 'Delete Frontend Engineer.docx')
      await expect(modal, 'Open deletion confirmation')
      click(modal(), 'Confirm deletion')
      await expect(() => uploads.querySelector('[role=status]')?.textContent.includes('Deleted'), 'DOCX deletion succeeded')
      await expect(() => !uploads.querySelector('[aria-label="Delete Frontend Engineer.docx"]'), 'DOCX removed immediately')
      location.hash = 'jd-analyzer'
      const matching = document.querySelector('#resume-matching')
      await expect(() => matching.checkVisibility() && matching.querySelectorAll('select')[1].selectedOptions[0]?.textContent.includes('Studio check'), 'JD selected')
      const jobs = matching.querySelector('.source-management')
      jobs.open = true
      click(jobs, 'Delete Studio check')
      await expect(modal, 'Open deletion confirmation')
      click(modal(), 'Confirm deletion')
      await expect(() => !matching.querySelectorAll('select')[1].value
        && !jobs.querySelector('[aria-label="Delete Studio check"]'), 'Deleted JD is removed and selection clears')
      location.hash = 'active-master'
      await expect(() => uploads.checkVisibility(), 'Return to master')
      click(uploads, 'Delete Software Engineer.pdf')
      await expect(modal, 'Open deletion confirmation')
      click(modal(), 'Confirm deletion')
      await expect(() => document.querySelector('.master-panel form select').value === ''
        && !uploads.querySelector('[aria-label="Delete Software Engineer.pdf"]'), 'Deleted master clears selection')
    })()`,
  })
  assert.equal(sourceDeletion.exceptionDetails, undefined, JSON.stringify(sourceDeletion.exceptionDetails))
  backendProcess.stdin.end('\n')
  await closed(backendProcess)
  assert.equal(backendProcess.exitCode, 0, 'Backend exited after source deletion')
  backendProcess = startBackend()
  await waitFor(() => ready(`${api}/api/health`), 'backend restarted after source deletion')
  await devtools('Page.reload')
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: `document.querySelector('.master-panel .source-management summary')?.textContent.endsWith('(0)')
      && [...document.querySelectorAll('#resume-matching select option')].some(option => option.textContent.includes('Infrastructure Engineer'))
      && ![...document.querySelectorAll('#resume-matching select option')].some(option => option.textContent.includes('Studio check'))`,
  }).catch(() => ({result: {}}))).result.value, 'Archived sources stay hidden after backend restart')
  await devtools('Runtime.evaluate', { expression: "location.hash = 'my-resumes'" })
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: "document.querySelectorAll('.library-grid .resume-card').length === 3 && !!document.querySelector('.mini-sheet canvas:not([hidden])')",
  })).result.value, 'Saved PDFs still render after source deletion and restart')
  await devtools('Runtime.evaluate', { expression: `(() => {
    const profile = document.querySelector('.profile-menu');
    profile.querySelector('summary').click();
    [...profile.querySelectorAll('button')].find((item) => item.textContent === 'Lock workspace').click();
  })()` })
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: 'document.querySelector("#workspace-token") !== null',
  })).result.value, 'workspace locks and returns to login')
  assert.equal((await localGet(`${api}/api/matches/sources`)).status, 401)
  await devtools('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
  await devtools('Runtime.evaluate', { expression: "location.hash = 'how-it-works'" })
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: "document.querySelectorAll('.story-step').length === 5 && getComputedStyle(document.querySelector('.story-sheet')).animationName === 'none'",
  })).result.value, 'Public walkthrough with reduced motion')
  await devtools('Runtime.evaluate', { expression: "document.querySelector('.story-step:last-child').scrollIntoView({block: 'center'})" })
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: "document.querySelector('.story-frame')?.dataset.scene === '4'",
  })).result.value, 'Walkthrough reaches saved versions')
  await devtools('Runtime.evaluate', { expression: "document.querySelector('.story-step a').click()" })
  await waitFor(async () => (await devtools('Runtime.evaluate', {
    expression: "location.hash === '#jd-analyzer' && !!document.querySelector('#workspace-token')",
  })).result.value, 'Walkthrough opens login for analyzer')
  assert.deepEqual(exceptions, [], 'Unexpected browser runtime exceptions')
  console.log('Browser passed workspace access, theme and responsive navigation, PDF/DOCX workflows, JD analysis, Studio, and saved versions.')
} finally {
  const exits = [backendProcess, vite, edge].map(closed)
  backendProcess.stdin.end('\n')
  vite.kill()
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ id: ++messageId, method: 'Browser.close' }))
    socket.close()
  } else {
    edge.kill()
  }
  await Promise.race([Promise.all(exits), delay(5000)])
  if (profile.startsWith(tempRoot + sep + 'jd-analyzer-edge-')) {
    await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 })
  }
}
