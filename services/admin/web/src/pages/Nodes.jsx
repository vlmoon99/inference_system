import { useState } from 'react'
import { api, usePoll } from '../api.js'
import { Bar, Button, Card, Err } from '../ui.jsx'

function Gpu({ g, unified, mem }) {
  return (
    <div className="space-y-2 rounded-lg bg-zinc-950 p-3 text-sm">
      <div className="flex justify-between"><span className="font-medium">{g.name}</span>
        <span className="text-zinc-400">{g.temp_c ?? '–'}°C · {g.power_w ?? '–'} W</span></div>
      <Bar label="GPU util %" value={g.util} max={100} />
      {unified
        ? <Bar label="unified memory GB" value={mem?.used_gb} max={mem?.total_gb} />
        : <Bar label="VRAM MB" value={g.mem_used_mb} max={g.mem_total_mb} />}
    </div>
  )
}

export default function Nodes({ onUnauthorized }) {
  const [nodes, setNodes] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState('')
  const [logs, setLogs] = useState(null)
  const load = () => api('/api/nodes').then((n) => { setNodes(n); setError(null) })
    .catch((e) => (e.status === 401 ? onUnauthorized() : setError(e)))
  usePoll(load, 3000)

  const act = async (node, name, action) => {
    if (action === 'stop' && !confirm(`Stop ${name} on ${node}?`)) return
    setBusy(`${node}/${name}`)
    try { await api(`/api/nodes/${node}/containers/${name}/${action}`, { method: 'POST' }); await load() }
    catch (e) { setError(e) } finally { setBusy('') }
  }
  const showLogs = async (node, name) => {
    try { setLogs({ name: `${node}/${name}`, ...(await api(`/api/nodes/${node}/containers/${name}/logs?tail=300`)) }) }
    catch (e) { setError(e) }
  }

  return (
    <div className="space-y-4">
      <Err error={error} />
      {nodes.map((n) => (
        <Card key={n.name} title={n.name}
          right={n.ok ? <span className="text-xs text-zinc-400">load {n.system.load.map((x) => x.toFixed(1)).join(' ')} · {n.system.cpus} CPU</span>
            : <span className="text-xs text-red-400">unreachable</span>}>
          {!n.ok ? <p className="text-sm text-red-400">{n.error}</p> : (
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
              <div className="space-y-2">
                {n.system.gpus.map((g) => <Gpu key={g.index} g={g} unified={n.system.unified} mem={n.system.mem} />)}
                {!n.system.unified && <Bar label="RAM GB" value={n.system.mem.used_gb} max={n.system.mem.total_gb} />}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-zinc-500"><tr><th className="py-1">container</th><th>state</th><th>restarts</th><th /></tr></thead>
                  <tbody>
                    {n.containers.map((c) => (
                      <tr key={c.name} className="border-t border-zinc-800">
                        <td className="py-1.5">{c.name}</td>
                        <td className={c.state === 'running' ? 'text-emerald-400' : 'text-amber-400'}>{c.state}{c.health ? ` (${c.health})` : ''}</td>
                        <td>{c.restarts}</td>
                        <td className="space-x-1 whitespace-nowrap text-right">
                          <Button kind="ghost" onClick={() => showLogs(n.name, c.name)}>logs</Button>
                          {c.state === 'running'
                            ? <><Button kind="ghost" disabled={busy} onClick={() => act(n.name, c.name, 'restart')}>restart</Button>
                                <Button kind="danger" disabled={busy} onClick={() => act(n.name, c.name, 'stop')}>stop</Button></>
                            : <Button disabled={busy} onClick={() => act(n.name, c.name, 'start')}>start</Button>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </Card>
      ))}
      {logs && (
        <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/70 p-4" onClick={() => setLogs(null)}>
          <div className="max-h-[85vh] w-full max-w-5xl overflow-auto rounded-xl bg-zinc-950 p-4" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex justify-between"><b>{logs.name}</b><Button kind="ghost" onClick={() => setLogs(null)}>close</Button></div>
            <pre className="text-xs leading-5 whitespace-pre-wrap text-zinc-300">{logs.lines.join('\n')}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
