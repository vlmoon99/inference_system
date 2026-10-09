import { useEffect } from 'react'

// Thin fetch wrapper: JSON in/out, throws Error(detail) on non-2xx. 401 → the app shows the login screen.
export async function api(path, { method = 'GET', body } = {}) {
  const r = await fetch(path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) {
    const err = new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${r.status}`)
    err.status = r.status
    throw err
  }
  return data
}

export function usePoll(fn, ms, deps = []) {
  // re-run fn every ms while the tab is visible
  useEffect(() => {
    let alive = true
    const tick = () => { if (alive && document.visibilityState === 'visible') fn() }
    tick()
    const id = setInterval(tick, ms)
    return () => { alive = false; clearInterval(id) }
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps
}
