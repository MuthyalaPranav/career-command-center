import { useEffect, useRef, useState } from 'react'
import { Icon } from './workspaceUI'

const scenes = [
  ['Master resume', 'Your Resume. Your Career Intelligence.', 'Start with the document that holds your experience, projects, and skills.'],
  ['Extraction', 'Find the skills in your experience.', 'The workspace extracts text and groups skills from your source document.'],
  ['JD comparison', 'See where your experience fits.', 'Compare resume evidence with job requirements and identify gaps.'],
  ['AI optimization', 'Review every suggested change.', 'Choose wording that highlights relevant experience before preparing an ATS-friendly export.'],
  ['Resume library', 'Optimize Once. Reuse Intelligently.', 'Save a version for this role and revisit it when a similar opportunity comes along.'],
]

export default function Walkthrough() {
  const [scene, setScene] = useState(0)
  const steps = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let frame = 0
    function update() {
      frame = 0
      const midpoint = window.innerHeight * .55
      let current = 0
      Array.from(steps.current?.children ?? []).forEach((step, index) => {
        if (step.getBoundingClientRect().top <= midpoint) current = index
      })
      setScene(current)
    }
    function schedule() {
      if (!frame) frame = requestAnimationFrame(update)
    }
    update()
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
    }
  }, [])
  return <section className="walkthrough">
    <header><h1>How it works</h1><p>An illustrated guide to tailoring a resume.</p></header>
    <div className="story-layout">
      <div className="story-frame" data-scene={scene} aria-hidden="true">
        <div className="story-stack" /><div className="story-sheet"><Icon name="file" /><b>{scene >= 3 ? 'Tailored resume' : 'Master resume'}</b>
          <small>Experience</small><i/><i/><small>Projects</small><i/><i/></div>
        <div className="story-labels">{['Python', 'React', 'FastAPI', 'AI', 'Machine Learning'].map(skill => <span key={skill}>{skill}</span>)}</div>
        <div className="story-jd"><b>Job description</b><span>Python · Matched</span><small>SQL · Missing</small></div>
      </div>
      <div ref={steps}>{scenes.map(([label, heading, copy], index) => <section className="story-step" data-step={index} key={label}>
        <small>{label}</small><h2>{heading}</h2><p>{copy}</p>
        {index === 4 && <a className="primary-button" href="#jd-analyzer">Analyze a job description</a>}
      </section>)}</div>
    </div>
  </section>
}
