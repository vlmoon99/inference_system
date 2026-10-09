import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Button, Card, Err, Input } from '../ui.jsx'

const toB64 = (file) => new Promise((ok, bad) => {
  const r = new FileReader()
  r.onload = () => ok(String(r.result).split(',')[1])
  r.onerror = bad
  r.readAsDataURL(file)
})

export default function Playground({ onUnauthorized }) {
  const [models, setModels] = useState([])
  const [prompt, setPrompt] = useState('')
  const [chatOut, setChatOut] = useState('')
  const [imgPrompt, setImgPrompt] = useState('')
  const [photo, setPhoto] = useState(null)
  const [img, setImg] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState(null)
  const fail = (e) => (e.status === 401 ? onUnauthorized() : setError(e))
  useEffect(() => { api('/api/models').then(setModels).catch(fail) }, [])
  const chatModel = models.find((m) => m.mode === 'chat')?.name
  const imageModel = models.find((m) => m.mode === 'image_generation')?.name

  const chat = async () => {
    setBusy('chat'); setError(null); setChatOut('')
    const t = Date.now()
    try {
      const r = await api('/api/play/chat', { method: 'POST', body: { model: chatModel, messages: [{ role: 'user', content: prompt }] } })
      setChatOut(`${r.choices[0].message.content}\n\n— ${r.usage?.completion_tokens} tokens in ${((Date.now() - t) / 1000).toFixed(1)} s`)
    } catch (e) { fail(e) } finally { setBusy('') }
  }
  const image = async () => {
    setBusy('image'); setError(null); setImg(null)
    const t = Date.now()
    try {
      const body = { model: imageModel, prompt: imgPrompt, size: '1024x1024' }
      if (photo) body.image_b64 = await toB64(photo)
      const r = await api('/api/play/image', { method: 'POST', body })
      setImg({ src: `data:image/png;base64,${r.data[0].b64_json}`, s: ((Date.now() - t) / 1000).toFixed(1) })
    } catch (e) { fail(e) } finally { setBusy('') }
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Err error={error} />
      <Card title={`Chat · ${chatModel || '…'}`}>
        <textarea className="h-28 w-full rounded-md border border-zinc-700 bg-zinc-950 p-2 text-sm" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <Button className="mt-2" disabled={!prompt || busy} onClick={chat}>{busy === 'chat' ? 'thinking…' : 'Send'}</Button>
        {chatOut && <pre className="mt-3 text-sm whitespace-pre-wrap text-zinc-300">{chatOut}</pre>}
      </Card>
      <Card title={`Image · ${imageModel || '…'}`}>
        <Input placeholder={photo ? 'what to change about the photo' : 'describe the picture'} value={imgPrompt} onChange={(e) => setImgPrompt(e.target.value)} />
        <input type="file" accept="image/*" className="mt-2 text-sm text-zinc-400" onChange={(e) => setPhoto(e.target.files[0] || null)} />
        <Button className="mt-2" disabled={!imgPrompt || busy} onClick={image}>{busy === 'image' ? 'rendering…' : 'Render'}</Button>
        {img && <><img className="mt-3 rounded-lg" src={img.src} alt="" /><p className="mt-1 text-xs text-zinc-500">{img.s} s</p></>}
      </Card>
    </div>
  )
}
