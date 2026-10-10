import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Button, Card, Err, Input } from '../ui.jsx'

export default function Projects({ onUnauthorized }) {
  const [rows, setRows] = useState([])
  const [models, setModels] = useState([])
  const [name, setName] = useState('')
  const [created, setCreated] = useState(null)
  const [error, setError] = useState(null)
  const fail = (e) => (e.status === 401 ? onUnauthorized() : setError(e))
  const load = () => Promise.all([api('/api/projects'), api('/api/models')])
    .then(([p, m]) => { setRows(p); setModels(m) }).catch(fail)
  useEffect(() => { load() }, [])

  const create = async (e) => {
    e.preventDefault()
    try { setCreated(await api('/api/projects', { method: 'POST', body: { name } })); setName(''); load() } catch (err) { fail(err) }
  }
  const revoke = async (r) => {
    if (!confirm(`Revoke the key of ${r.project}? Its apps stop working immediately.`)) return
    try { await api(`/api/projects/${r.token}/revoke`, { method: 'POST' }); load() } catch (err) { fail(err) }
  }
  const limits = async (r) => {
    const v = prompt(`Limits of ${r.project}: requests per minute, parallel requests`, `${r.rpm_limit ?? 30}, ${r.max_parallel_requests ?? 2}`)
    if (!v) return
    const [rpm, par] = v.split(',').map((x) => parseInt(x, 10))
    try { await api(`/api/projects/${r.token}/limits`, { method: 'POST', body: { rpm_limit: rpm, max_parallel_requests: par } }); load() } catch (err) { fail(err) }
  }
  return (
    <div className="space-y-4">
      <Err error={error} />
      <Card title="New project">
        <form onSubmit={create} className="flex gap-2">
          <Input placeholder="project name, e.g. boostcontent" value={name} onChange={(e) => setName(e.target.value)} />
          <Button type="submit" disabled={!name.trim()}>Create key</Button>
        </form>
        {created && (
          <div className="mt-3 rounded-lg border border-amber-600/50 bg-amber-950/30 p-3 text-sm">
            <p className="mb-1 text-amber-300">Key for <b>{created.project}</b>. Copy it now: it is shown only once.</p>
            <code className="break-all select-all">{created.key}</code>
          </div>
        )}
      </Card>
      <Card title="Projects">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-zinc-500"><tr><th className="py-1">project</th><th>key</th><th>created</th><th>per minute</th><th>parallel</th><th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.token} className="border-t border-zinc-800">
                <td className="py-1.5">{r.project || '(no name)'}</td>
                <td className="font-mono text-xs text-zinc-400">{r.key_hint}</td>
                <td className="text-zinc-400">{r.created_at?.slice(0, 16).replace('T', ' ')}</td>
                <td>{r.rpm_limit ?? 'no limit'}</td>
                <td>{r.max_parallel_requests ?? 'no limit'}</td>
                <td className="space-x-2 text-right"><Button onClick={() => limits(r)}>limits</Button><Button kind="danger" onClick={() => revoke(r)}>revoke</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Models (gateway: http://100.64.0.1:8000/v1)">
        <ul className="space-y-1 text-sm">
          {models.map((m, i) => <li key={i}><b>{m.name}</b> <span className="text-zinc-500">{m.mode} · {m.api_base}</span></li>)}
        </ul>
      </Card>
    </div>
  )
}
