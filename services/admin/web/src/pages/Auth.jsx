import { useState } from 'react'
import { api } from '../api.js'
import { Button, Card, Err, Input } from '../ui.jsx'

export default function Auth({ setup, onDone }) {
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [error, setError] = useState(null)
  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    if (setup && pw !== pw2) return setError(new Error('passwords differ'))
    try {
      await api(setup ? '/api/auth/setup' : '/api/auth/login', { method: 'POST', body: { password: pw } })
      onDone()
    } catch (err) { setError(err) }
  }
  return (
    <div className="mx-auto mt-24 max-w-sm p-4">
      <Card title={setup ? 'Set the admin password' : 'Inference admin'}>
        <form onSubmit={submit} className="space-y-3">
          {setup && <p className="text-sm text-zinc-400">First visit: the password you choose now protects this console. At least 10 characters.</p>}
          <Input type="password" autoFocus placeholder="password" value={pw} onChange={(e) => setPw(e.target.value)} />
          {setup && <Input type="password" placeholder="repeat password" value={pw2} onChange={(e) => setPw2(e.target.value)} />}
          <Err error={error} />
          <Button type="submit" className="w-full">{setup ? 'Set password' : 'Log in'}</Button>
        </form>
      </Card>
    </div>
  )
}
