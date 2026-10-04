/**
 * Browser notification setup (internal Web Push).
 *
 * The only user-facing action is "Enable Notifications". Everything here —
 * permission, service worker, the browser's internal push registration and
 * its storage on the server — is an implementation detail and is never
 * described as a "subscription" in the UI.
 */
import { API_BASE } from './api'

const SW_PATH = '/service-worker.js'

export function notificationsSupported(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

export function permissionState(): NotificationPermission | 'unsupported' {
  return notificationsSupported() ? Notification.permission : 'unsupported'
}

/** urlBase64 -> Uint8Array (own ArrayBuffer) for PushManager.subscribe */
function urlB64ToUint8Array(b64: string): Uint8Array<ArrayBuffer> {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const raw = window.atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const buf = new ArrayBuffer(raw.length)
  const out = new Uint8Array(buf)
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

async function backendPublicKey(): Promise<string> {
  const r = await fetch(`${API_BASE}/api/notify/public-key`)
  if (!r.ok) throw new Error('Notifications are not configured on this server yet.')
  const j = await r.json()
  return j.publicKey as string
}

/**
 * Full enable flow: permission -> service worker -> internal push registration
 * -> stored server-side against the district the user last viewed (optional).
 */
export async function enableNotifications(districtId?: number): Promise<
  { ok: true; state: 'granted' } | { ok: false; reason: string }
> {
  if (!notificationsSupported()) {
    return { ok: false, reason: 'This browser does not support notifications.' }
  }
  // ask first (must be from the click handler), then register everything
  const perm = await Notification.requestPermission()
  if (perm !== 'granted') {
    return { ok: false, reason: 'Notifications are disabled. You can enable them from your browser settings.' }
  }
  try {
    const reg = await navigator.serviceWorker.register(SW_PATH)
    await navigator.serviceWorker.ready

    let sub = await reg.pushManager.getSubscription()
    if (!sub) {
      const key = await backendPublicKey()
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(key),
      })
    }
    const j = sub.toJSON()
    const r = await fetch(`${API_BASE}/api/notify/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: sub.endpoint, keys: j.keys, district_id: districtId ?? null }),
    })
    if (!r.ok) {
      const t = await r.text()
      throw new Error(`Server could not store the notification endpoint (${r.status}): ${t.slice(0, 120)}`)
    }
    return { ok: true, state: 'granted' }
  } catch (e) {
    return { ok: false, reason: String((e as Error).message || e) }
  }
}

/** Trigger a controlled test notification through the real delivery path. */
export async function sendTestNotification(): Promise<{ sent: number; used: string }> {
  const r = await fetch(`${API_BASE}/api/notify/test`, { method: 'POST' })
  if (!r.ok) throw new Error(`Test failed (${r.status})`)
  return r.json()
}

/** Best-effort cleanup when the user disables notifications in the browser. */
export async function syncPermissionState(): Promise<void> {
  if (!notificationsSupported()) return
  if (Notification.permission !== 'granted') {
    const reg = await navigator.serviceWorker.getRegistration()
    const sub = await reg?.pushManager.getSubscription()
    if (sub) {
      await fetch(`${API_BASE}/api/notify/unregister`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ endpoint: sub.endpoint }),
      })
      await sub.unsubscribe().catch(() => {})
    }
  }
}
