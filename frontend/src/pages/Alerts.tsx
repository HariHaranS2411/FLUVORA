import { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { AlertItem } from '../types'
import { Card } from '../components/Bits'
import {
  enableNotifications, notificationsSupported, permissionState,
  sendTestNotification, syncPermissionState,
} from '../services/notify'

type NotifyState = 'unsupported' | 'denied' | 'prompt' | 'enabled'

export default function Alerts() {
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [notify, setNotify] = useState<NotifyState>(() => {
    const p = permissionState()
    return p === 'granted' ? 'enabled' : p === 'denied' ? 'denied' : p === 'unsupported' ? 'unsupported' : 'prompt'
  })
  const [devices, setDevices] = useState<number | null>(null)

  const refreshDevices = () => {
    api.notifyStatus().then((s) => setDevices(s.active_browsers)).catch(() => setDevices(null))
  }

  useEffect(() => {
    api.alerts().then(setAlerts).catch(() => {})
    refreshDevices()
    syncPermissionState().catch(() => {})
  }, [])

  const enable = async () => {
    setBusy(true)
    setErr('')
    setMsg('')
    const res = await enableNotifications()
    if (!res.ok) {
      setBusy(false)
      setMsg('')
      setErr(res.reason)
      if (res.reason.includes('browser settings')) setNotify('denied')
      return
    }
    setNotify('enabled')
    refreshDevices()
    // Prove delivery immediately: the test push goes through the exact path a
    // real HIGH-risk alert uses, so the user sees a real notification now.
    try {
      const r = await sendTestNotification()
      setMsg(
        r.sent > 0
          ? `✅ Notifications enabled — a test notification was just delivered to this browser (${r.used}).`
          : '✅ Notifications enabled — this browser is registered. A notification will appear here when any district reaches HIGH risk.',
      )
    } catch {
      setMsg('✅ Notifications enabled — you will be alerted here and in this browser when any district reaches HIGH risk.')
    } finally {
      setBusy(false)
    }
  }

  const test = async () => {
    setBusy(true)
    setErr('')
    try {
      const r = await sendTestNotification()
      refreshDevices()
      if (r.sent > 0) {
        setMsg(`Test delivered to ${r.sent} browser${r.sent === 1 ? '' : 's'} — ${r.used}.`)
      } else if (!notificationsSupported()) {
        setErr('This browser cannot receive notifications. Open the site in a regular Chrome or Edge window and click 🔔 Enable Notifications there.')
      } else if (permissionState() === 'denied') {
        setErr('No device is enabled yet: this browser has notifications blocked. Allow them via the 🔒 icon in the address bar (then press "Check again"), or open the site in a regular Chrome/Edge window and click 🔔 Enable Notifications.')
      } else {
        setErr('No device is enabled yet. Click 🔔 Enable Notifications above and choose Allow — this browser then becomes an active device and the test will reach it.')
      }
    } catch (e) {
      setErr(String((e as Error).message || e))
    } finally {
      setBusy(false)
    }
  }

  // Re-read the live permission state — used after the user flips the setting
  // in their browser (browsers never re-prompt on their own after a denial).
  const recheck = () => {
    const p = permissionState()
    setNotify(p === 'granted' ? 'enabled' : p === 'denied' ? 'denied' : p === 'unsupported' ? 'unsupported' : 'prompt')
  }

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-bold">Alerts</h1>

      <Card title="Notifications">
        {notify === 'unsupported' ? (
          <p className="text-sm text-night-300">
            This browser does not support notifications. The Alerts list below always shows the
            latest HIGH-risk districts.
          </p>
        ) : notify === 'denied' ? (
          <div className="space-y-2">
            <p className="text-sm text-amber-300">
              Notifications are blocked for this site. You can enable them from your browser settings:
            </p>
            <ol className="text-xs text-night-300 list-decimal ml-4 space-y-0.5">
              <li>Click the <span className="text-night-100">🔒 lock</span> (or sliders) icon at the left of the address bar</li>
              <li>Open <span className="text-night-100">Site settings</span> and set <span className="text-night-100">Notifications</span> to <span className="text-night-100">Allow</span></li>
              <li>Come back here and press “Check again”</li>
            </ol>
            <button
              className="border border-night-500 rounded-md px-4 py-2 text-sm text-night-100 hover:bg-night-700"
              onClick={recheck}
            >
              Check again
            </button>
            <p className="text-xs text-night-400">
              If your browser offers no such option (some embedded/preview browsers block
              notifications entirely), open this page in a regular Chrome or Edge window — the
              allow prompt will appear there.
            </p>
          </div>
        ) : notify === 'enabled' ? (
          <div className="space-y-2">
            <p className="text-sm text-green-400 font-medium">✅ Notifications enabled</p>
            <p className="text-xs text-night-400">
              You will get a browser notification when any district reaches HIGH risk — even when
              this site is closed. You can turn this off anytime in your browser settings.
            </p>
            <button
              className="border border-night-500 rounded-md px-4 py-2 text-sm text-night-100 hover:bg-night-700 disabled:opacity-50"
              onClick={test} disabled={busy}
            >
              Send a test notification
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            <button
              className="bg-sky-600 text-white rounded-md px-4 py-2 text-sm hover:bg-sky-500 disabled:opacity-50"
              onClick={enable} disabled={busy}
            >
              {busy ? 'Enabling…' : '🔔 Enable Notifications'}
            </button>
            <p className="text-xs text-night-400">
              Get an automatic browser notification when any district reaches HIGH flood risk —
              no account needed, works even when this site is closed.
            </p>
          </div>
        )}

        {msg && <p className="text-sm text-night-200 mt-2">{msg}</p>}
        {err && <p className="text-sm text-amber-300 mt-2">{err}</p>}

        {devices !== null && (
          <p className="text-xs text-night-400 mt-2">
            Active devices receiving HIGH-risk notifications: <b className="text-night-200">{devices}</b>
            {devices === 0 && ' — enable notifications on this device to see it listed here.'}
          </p>
        )}

        <p className="text-xs text-night-400 mt-3">
          Alerts fire only when the model's high-flow estimate crosses the documented thresholds
          with sufficient data quality; entering the HIGH band requires stronger evidence than
          remaining in it (hysteresis), and duplicate alerts for an unchanged condition are
          suppressed (12 h cooldown). The same deduplication applies to notifications — a new
          HIGH-risk event notifies you once, not on every refresh.
        </p>
      </Card>

      <Card title="Recent alerts">
        {alerts.length === 0 ? (
          <p className="text-sm text-night-400">
            No alerts recorded. This is expected when all district risks are below the WATCH
            threshold or before the first risk computation run.
          </p>
        ) : (
          <ul className="space-y-3">
            {alerts.map((a) => (
              <li key={a.id} className="border border-night-600 rounded-lg p-3 text-sm whitespace-pre-line">
                <div className="font-semibold">
                  {a.alert_type.replace('_', ' ')} · {a.district}, {a.state}
                </div>
                <div className="text-night-400 text-xs mb-1">{new Date(a.created_at).toLocaleString()}</div>
                {a.message}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
