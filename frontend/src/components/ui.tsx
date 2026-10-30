import {
  CheckCircle,
  WarningCircle,
  Circle,
  Clock,
  ArrowClockwise,
  MagnifyingGlass,
  ArrowRight,
} from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import type { RunSummary } from '../api/client'

export function Notice({
  children,
  tone = 'info',
}: {
  children: ReactNode
  tone?: 'info' | 'error'
}) {
  return (
    <div
      className={`notice ${tone === 'error' ? 'notice-error' : ''}`}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      <WarningCircle size={19} className="mt-0.5 shrink-0" />
      <div>{children}</div>
    </div>
  )
}

const outcomeLabels: Record<string, string> = {
  selected: 'Suitable feature found',
  no_safe_fit: 'No suitable fit',
  passed: 'Check passed',
  failed: 'Stopped with an error',
  interrupted: 'Interrupted',
  baseline_failed: 'Original tests did not pass',
  verified: 'Patch verified',
  already_present: 'Behavior already present',
  exhausted: 'Repair allowance exhausted',
  running: 'In progress',
  queued: 'Queued',
}
export function RunBadge({ run }: { run: Pick<RunSummary, 'status' | 'outcome'> }) {
  const state = run.status === 'finished' ? (run.outcome ?? 'finished') : run.status
  const Icon = ['selected', 'passed', 'verified', 'already_present'].includes(state)
    ? CheckCircle
    : ['failed', 'interrupted', 'no_safe_fit', 'baseline_failed', 'exhausted'].includes(state)
      ? WarningCircle
      : Clock
  return (
    <span
      className={`badge ${['selected', 'passed', 'verified', 'already_present'].includes(state) ? 'badge-positive' : ['failed', 'interrupted', 'baseline_failed', 'exhausted'].includes(state) ? 'badge-error' : ''}`}
    >
      <Icon size={15} weight="regular" />
      {outcomeLabels[state] ?? state}
    </span>
  )
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="space-y-4 py-6">
      {Array.from({ length: rows }, (_, i) => (
        <div className="skeleton" style={{ width: `${96 - (i % 3) * 17}%` }} key={i} />
      ))}
      <span className="sr-only">Loading workspace</span>
    </div>
  )
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <MagnifyingGlass size={30} />
      </div>
      <h2>{title}</h2>
      <p>{children}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="space-y-4">
      <Notice tone="error">{message}</Notice>
      {retry && (
        <button className="button-secondary" onClick={retry}>
          <ArrowClockwise size={17} />
          Try again
        </button>
      )}
    </div>
  )
}

export function StatusMark({ passed }: { passed: boolean | null }) {
  return passed === true ? (
    <CheckCircle size={19} className="text-positive shrink-0" />
  ) : passed === false ? (
    <WarningCircle size={19} className="text-accent shrink-0" />
  ) : (
    <Circle size={19} className="text-muted shrink-0" />
  )
}

export function SectionHeading({
  title,
  children,
  action,
}: {
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="section-heading">
      <div>
        <h2>{title}</h2>
        {children && <p>{children}</p>}
      </div>
      {action}
    </div>
  )
}

export function TextAction({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button className="text-action" onClick={onClick}>
      {children}
      <ArrowRight size={15} />
    </button>
  )
}
