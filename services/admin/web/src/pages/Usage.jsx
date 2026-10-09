import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Card, Err } from '../ui.jsx'

export default function Usage({ onUnauthorized }) {
  const [days, setDays] = useState(7)
  const [rows, setRows] = useState([])
  const [error, setError] = useState(null)
  useEffect(() => {
    api(`/api/usage?days=${days}`).then(setRows).catch((e) => (e.status === 401 ? onUnauthorized() : setError(e)))
  }, [days])
  return (
    <Card title="Usage per project" right={
      <select className="rounded bg-zinc-800 px-2 py-1 text-sm" value={days} onChange={(e) => setDays(+e.target.value)}>
        {[1, 7, 30, 90].map((d) => <option key={d} value={d}>last {d} days</option>)}
      </select>}>
      <Err error={error} />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-zinc-500">
            <tr><th className="py-1">day</th><th>project</th><th>model</th><th>call</th><th className="text-right">requests</th>
              <th className="text-right">tokens in</th><th className="text-right">tokens out</th><th className="text-right">avg ms</th><th className="text-right">failed</th></tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-zinc-800">
                <td className="py-1.5">{r.day}</td><td>{r.project}</td><td>{r.model}</td><td className="text-zinc-400">{r.call_type}</td>
                <td className="text-right">{r.requests}</td><td className="text-right">{r.prompt_tokens}</td>
                <td className="text-right">{r.completion_tokens}</td><td className="text-right">{r.avg_ms}</td>
                <td className={`text-right ${r.failures ? 'text-red-400' : ''}`}>{r.failures}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={9} className="py-4 text-center text-zinc-500">no requests yet</td></tr>}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
