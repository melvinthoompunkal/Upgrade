import React, { lazy } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { IconContext } from '@phosphor-icons/react'
import { MotionConfig } from 'motion/react'
import { WorkspaceProvider } from './app/workspace-context'
import { Shell } from './app/Shell'
import { NewRun } from './features/new-run/NewRun'
import { ProjectRun } from './features/new-run/ProjectRun'
import { Empty } from './components/ui'
import '@fontsource/ibm-plex-sans/latin-400.css'
import '@fontsource/ibm-plex-sans/latin-500.css'
import '@fontsource/ibm-plex-sans/latin-600.css'
import '@fontsource/ibm-plex-mono/latin-400.css'
import './app/styles.css'

const History = lazy(() =>
  import('./features/runs/History').then((module) => ({ default: module.History })),
)
const RunWorkspace = lazy(() =>
  import('./features/runs/RunWorkspace').then((module) => ({ default: module.RunWorkspace })),
)
const SystemChecks = lazy(() =>
  import('./features/system/SystemChecks').then((module) => ({ default: module.SystemChecks })),
)
const Repositories = lazy(() =>
  import('./features/account/Repositories').then((module) => ({ default: module.Repositories })),
)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <IconContext.Provider value={{ weight: 'regular' }}>
      <MotionConfig
        reducedMotion="user"
        transition={{ type: 'spring', stiffness: 280, damping: 30 }}
      >
        <BrowserRouter>
          <WorkspaceProvider>
            <Routes>
              <Route element={<Shell />}>
                <Route index element={<ProjectRun />} />
                <Route path="example" element={<NewRun />} />
                <Route path="runs" element={<History />} />
                <Route path="runs/:runId/:view?" element={<RunWorkspace />} />
                <Route path="system" element={<SystemChecks />} />
                <Route path="repositories" element={<Repositories />} />
                <Route
                  path="*"
                  element={
                    <Empty title="Page not found">
                      Choose New run or Runs in the navigation to continue.
                    </Empty>
                  }
                />
              </Route>
            </Routes>
          </WorkspaceProvider>
        </BrowserRouter>
      </MotionConfig>
    </IconContext.Provider>
  </React.StrictMode>,
)
