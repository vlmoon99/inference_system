import { useEffect, useState } from 'react'
import { api } from './api.js'
import Auth from './pages/Auth.jsx'
import Nodes from './pages/Nodes.jsx'
import Projects from './pages/Projects.jsx'
import Usage from './pages/Usage.jsx'
import Playground from './pages/Playground.jsx'

const TABS = { Nodes, Projects, Usage, Playground }

export default function App() {
  const [auth, setAuth] = useState(null)
  const [tab, setTab] = useState(() => { try { return localStorage.getItem('tab') || 'Nodes' } catch { return 'Nodes' } })
  const refresh = () => api('/api/auth/state').then(setAuth).catch(() => setAuth({ setup_needed: false, logged_in: false }))
  useEffect(() => { refresh() }, [])
  useEffect(() => { try { localStorage.setItem('tab', tab) } catch { /* private mode */ } }, [tab])

  if (!auth) return null
  if (!auth.logged_in) return <Auth setup={auth.setup_needed} onDone={refresh} />
  const Page = TABS[tab] || Nodes
  return (
    <div className="mx-auto max-w-6xl p-4">
      <header className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="mr-4 text-lg font-bold">Inference <span className="text-emerald-400">admin</span></h1>
        {Object.keys(TABS).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`rounded-md px-3 py-1.5 text-sm ${t === tab ? 'bg-zinc-100 text-zinc-900' : 'text-zinc-300 hover:bg-zinc-800'}`}>{t}</button>
        ))}
        <button className="ml-auto text-sm text-zinc-400 hover:text-zinc-200"
          onClick={() => api('/api/auth/logout', { method: 'POST' }).then(refresh)}>Log out</button>
      </header>
      <Page onUnauthorized={refresh} />
    </div>
  )
}
