import { useEffect, useState } from 'react'
import { Activity, Database, FolderOpen, Layers } from 'lucide-react'

type Stats = {
  totalHands: number
  totalWon: number
  gameTypes: { game_type: string; count: number }[]
}

type HHPath = { path: string; site: string }

type LogLine = { msg: string; type: string }

export default function App() {
  const [version, setVersion] = useState('…')
  const [stats, setStats] = useState<Stats | null>(null)
  const [paths, setPaths] = useState<HHPath[]>([])
  const [logs, setLogs] = useState<LogLine[]>([])
  const [dbError, setDbError] = useState<string | null>(null)

  useEffect(() => {
    const api = window.pokerAPI
    if (!api) {
      setDbError('pokerAPI not available — preload may have failed')
      return
    }

    api.getVersion().then(setVersion).catch(() => setVersion('unknown'))
    api.getActiveHHPaths().then(setPaths).catch(() => setPaths([]))

    api.getStats()
      .then((data) => {
        if (data) setStats(data)
        else setDbError('Database not ready yet')
      })
      .catch((err: Error) => setDbError(err.message))

    const unsubLog = api.onAppLog((line) => {
      setLogs((prev) => [line, ...prev].slice(0, 8))
    })

    return () => unsubLog()
  }, [])

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-950/80 px-6 py-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Poker Therapist Suite</h1>
            <p className="text-sm text-slate-400">LeakSnipe desktop — v{version}</p>
          </div>
          <div className="flex items-center gap-2 rounded-full bg-emerald-500/10 px-3 py-1 text-sm text-emerald-300">
            <Activity className="h-4 w-4" />
            Running
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 p-6 md:grid-cols-2">
        <section className="rounded-xl border border-slate-800 bg-slate-950 p-5">
          <div className="mb-4 flex items-center gap-2 text-slate-300">
            <Database className="h-5 w-5" />
            <h2 className="font-medium">Session stats</h2>
          </div>
          {dbError ? (
            <p className="text-sm text-amber-300">{dbError}</p>
          ) : stats ? (
            <dl className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-slate-400">Hands tracked</dt>
                <dd className="text-2xl font-semibold">{stats.totalHands}</dd>
              </div>
              <div>
                <dt className="text-slate-400">Net won</dt>
                <dd className="text-2xl font-semibold">
                  {stats.totalWon >= 0 ? '+' : ''}
                  {(stats.totalWon / 100).toFixed(2)}
                </dd>
              </div>
            </dl>
          ) : (
            <p className="text-sm text-slate-400">Loading database…</p>
          )}
        </section>

        <section className="rounded-xl border border-slate-800 bg-slate-950 p-5">
          <div className="mb-4 flex items-center gap-2 text-slate-300">
            <FolderOpen className="h-5 w-5" />
            <h2 className="font-medium">Watched hand-history folders</h2>
          </div>
          {paths.length === 0 ? (
            <p className="text-sm text-slate-400">No poker client folders detected yet.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {paths.map((p) => (
                <li key={p.path} className="rounded-lg bg-slate-900 px-3 py-2">
                  <span className="font-medium text-sky-300">{p.site}</span>
                  <div className="truncate text-slate-400">{p.path}</div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="md:col-span-2 rounded-xl border border-slate-800 bg-slate-950 p-5">
          <div className="mb-4 flex items-center gap-2 text-slate-300">
            <Layers className="h-5 w-5" />
            <h2 className="font-medium">Live activity</h2>
          </div>
          {logs.length === 0 ? (
            <p className="text-sm text-slate-400">
              Waiting for hand imports, cloud sync, or watcher events…
            </p>
          ) : (
            <ul className="space-y-1 font-mono text-xs text-slate-300">
              {logs.map((line, i) => (
                <li key={`${line.msg}-${i}`} className="truncate">
                  <span className="text-slate-500">[{line.type}]</span> {line.msg}
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  )
}
