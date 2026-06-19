/// <reference types="vite/client" />

interface PokerAPI {
  onNewHand: (callback: (data: { site: string; raw: string }) => void) => () => void
  onNewParsedHand: (callback: (data: unknown) => void) => () => void
  onAppLog: (callback: (data: { msg: string; type: string }) => void) => () => void
  getHands: (opts?: Record<string, unknown>) => Promise<unknown[]>
  getHandById: (id: string) => Promise<unknown>
  getSessions: (opts?: Record<string, unknown>) => Promise<unknown[]>
  getSessionHands: (sessionId: string) => Promise<unknown[]>
  getStats: () => Promise<{
    totalHands: number
    totalWon: number
    gameTypes: { game_type: string; count: number }[]
    recentResults: unknown[]
  } | null>
  importParsedHands: (hands: unknown[]) => Promise<unknown>
  analyzeSession: (sessionId: string) => Promise<unknown>
  analyzeRecentHands: (count?: number) => Promise<unknown>
  getCloudTargets: () => Promise<unknown[]>
  addCloudTarget: (target: unknown) => Promise<unknown>
  updateCloudTarget: (id: string, updates: unknown) => Promise<unknown>
  removeCloudTarget: (id: string) => Promise<unknown>
  detectCloudFolders: () => Promise<unknown[]>
  parseHandText: (text: string, site: string) => Promise<unknown>
  importFile: () => Promise<unknown>
  getDriveHudPath: () => Promise<string>
  getVersion: () => Promise<string>
  getHHClients: () => Promise<unknown[]>
  getActiveHHPaths: () => Promise<{ path: string; site: string }[]>
  addCustomHHPath: (p: string, site: string) => Promise<unknown>
  removeCustomHHPath: (p: string) => Promise<unknown>
  browseFolder: () => Promise<string | null>
  getLeakStats: (opts?: Record<string, unknown>) => Promise<unknown>
  getTiltFlags: (opts?: Record<string, unknown>) => Promise<unknown>
  getLeaks: (opts?: Record<string, unknown>) => Promise<unknown>
  getSummaries: (opts?: Record<string, unknown>) => Promise<unknown>
  addTag: (handId: string, tag: string) => Promise<unknown>
  removeTag: (handId: string, tag: string) => Promise<unknown>
  getTagsForHand: (handId: string) => Promise<unknown>
  getAllTags: () => Promise<unknown[]>
  getHandsByTag: (tag: string) => Promise<unknown[]>
  runBackup: () => Promise<unknown>
  getBackups: () => Promise<unknown[]>
  getBackupDir: () => Promise<string>
}

interface Window {
  pokerAPI: PokerAPI
}
