
import { app, BrowserWindow, ipcMain, screen, dialog } from 'electron';
import path from 'node:path';
import fs from 'fs';
import { initDatabase } from './db/index';
import { checkActiveWindows } from './tracking';
import { overlayManager } from './overlayManager';
import { writeHandSummary } from './handWriter';
import { registerIpcHandlers } from './ipcHandlers';
import { parseHandHistory } from './services/handParser';
import { CloudSyncManager } from './services/cloudSync';
import { AutoBackupService } from './services/autoBackup';
import { ELECTRON_ROOT } from './electronPaths';

let dbUrl: string;
let db: any;
let startHandHistoryWatcher: any;
let cloudSync: CloudSyncManager | null = null;
let autoBackup: AutoBackupService | null = null;

// Paths
let historyWatcher: any = null;

process.env.DIST = path.join(ELECTRON_ROOT, '../dist');
process.env.VITE_PUBLIC = app.isPackaged ? process.env.DIST : path.join(process.env.DIST, '../public');

let win: BrowserWindow | null;
const VITE_DEV_SERVER_URL = process.env['VITE_DEV_SERVER_URL'];

// ── Poker client hand history path discovery ──────────────────────

interface PokerClientPaths {
  name: string;
  site: string;
  paths: string[];
}

function discoverPokerClientPaths(): PokerClientPaths[] {
  const userProfile = process.env.USERPROFILE || 'C:\\Users\\mfane';
  const appData = process.env.APPDATA || path.join(userProfile, 'AppData', 'Roaming');
  const localAppData = process.env.LOCALAPPDATA || path.join(userProfile, 'AppData', 'Local');

  const clients: PokerClientPaths[] = [
    {
      name: 'DriveHUD2',
      site: 'DriveHUD2',
      paths: [
        path.join(appData, 'DriveHUD 2', 'ProcessedData'),
      ],
    },
    {
      name: 'CoinPoker',
      site: 'CoinPoker',
      paths: [
        // CoinPoker standard HH export locations
        path.join(localAppData, 'CoinPoker', 'HandHistory'),
        path.join(appData, 'CoinPoker', 'HandHistory'),
        path.join(userProfile, 'CoinPoker', 'HandHistory'),
        path.join(appData, 'CoinPoker', 'logs'),
        // Program Files install
        path.join('C:', 'CoinPoker', 'HandHistory'),
        path.join('C:', 'Program Files', 'CoinPoker', 'HandHistory'),
      ],
    },
    {
      name: 'BetACR / Americas Cardroom (WPN)',
      site: 'BetACR',
      paths: [
        // ACR standard hand history locations
        path.join(localAppData, 'WPN', 'HandHistory'),
        path.join(appData, 'Americas Cardroom', 'HandHistory'),
        path.join(localAppData, 'Americas Cardroom', 'HandHistory'),
        path.join(appData, 'WPN', 'HandHistory'),
        path.join(userProfile, 'AppData', 'Local', 'ACR', 'HandHistory'),
        path.join(userProfile, 'WPN', 'HandHistory'),
        // BetOnline (same network)
        path.join(appData, 'BetOnline', 'HandHistory'),
        path.join(localAppData, 'BetOnline', 'HandHistory'),
      ],
    },
  ];

  return clients;
}

function getActiveWatchPaths(): { path: string; site: string }[] {
  const clients = discoverPokerClientPaths();
  const active: { path: string; site: string }[] = [];

  for (const client of clients) {
    for (const p of client.paths) {
      try {
        if (fs.existsSync(p)) {
          active.push({ path: p, site: client.site });
        }
      } catch (_e) { /* skip inaccessible */ }
    }
  }

  // Also load user-configured custom paths from settings
  try {
    const settingsPath = path.join(app.getPath('userData'), 'hh-paths.json');
    if (fs.existsSync(settingsPath)) {
      const custom: { path: string; site: string }[] = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
      for (const c of custom) {
        if (fs.existsSync(c.path)) active.push(c);
      }
    }
  } catch (_e) { /* ignore */ }

  return active;
}

function createWindow() {
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'Poker Therapist Suite',
    icon: path.join(process.env.VITE_PUBLIC!, 'vite.svg'),
    webPreferences: {
      preload: path.join(ELECTRON_ROOT, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    backgroundColor: '#0f172a',
    show: false,
  });

  win.once('ready-to-show', () => win?.show());

  win.webContents.on('did-finish-load', () => {
    win?.webContents.send('main-process-message', new Date().toLocaleString());
  });

  if (VITE_DEV_SERVER_URL) {
    win.loadURL(VITE_DEV_SERVER_URL);
  } else {
    win.loadFile(path.join(process.env.DIST!, 'index.html'));
  }
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    overlayManager.destroy();
    app.quit();
    win = null;
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.whenReady().then(async () => {
  // Setup logging
  try {
    const logPath = path.join(app.getPath('userData'), 'poker-therapist.log');
    const logFile = fs.createWriteStream(logPath, { flags: 'a' });
    const origLog = console.log;
    const origErr = console.error;
    console.log = (...args) => { logFile.write(`[INFO] ${new Date().toISOString()} ${args.join(' ')}\n`); origLog(...args); };
    console.error = (...args) => { logFile.write(`[ERROR] ${new Date().toISOString()} ${args.join(' ')}\n`); origErr(...args); };
    console.log('=== Poker Therapist Suite starting ===');
  } catch (_e) { /* continue without file logging */ }

  // Register all IPC handlers (DB, Rex, CloudSync, Parser)
  try {
    registerIpcHandlers();
    console.log('IPC handlers registered');
  } catch (err) {
    console.error('Failed to register IPC handlers:', err);
  }

  // ── IPC: Poker client path management ──
  ipcMain.handle('hh:getClients', () => {
    const clients = discoverPokerClientPaths();
    return clients.map(c => ({
      name: c.name,
      site: c.site,
      paths: c.paths.map(p => ({ path: p, exists: fs.existsSync(p) })),
    }));
  });

  ipcMain.handle('hh:getActivePaths', () => getActiveWatchPaths());

  ipcMain.handle('hh:addCustomPath', (_e, p: string, site: string) => {
    const settingsPath = path.join(app.getPath('userData'), 'hh-paths.json');
    let custom: { path: string; site: string }[] = [];
    try { if (fs.existsSync(settingsPath)) custom = JSON.parse(fs.readFileSync(settingsPath, 'utf8')); } catch (_e) { /* ignore */ }
    if (!custom.find(c => c.path === p)) {
      custom.push({ path: p, site });
      fs.writeFileSync(settingsPath, JSON.stringify(custom, null, 2));
    }
    return custom;
  });

  ipcMain.handle('hh:removeCustomPath', (_e, p: string) => {
    const settingsPath = path.join(app.getPath('userData'), 'hh-paths.json');
    let custom: { path: string; site: string }[] = [];
    try { if (fs.existsSync(settingsPath)) custom = JSON.parse(fs.readFileSync(settingsPath, 'utf8')); } catch (_e) { /* ignore */ }
    custom = custom.filter(c => c.path !== p);
    fs.writeFileSync(settingsPath, JSON.stringify(custom, null, 2));
    return custom;
  });

  ipcMain.handle('hh:browseFolder', async () => {
    const result = await dialog.showOpenDialog({ properties: ['openDirectory'], title: 'Select Hand History Folder' });
    return result.canceled ? null : result.filePaths[0];
  });

  // Initialize cloud sync
  try {
    cloudSync = new CloudSyncManager();
    console.log('Cloud sync targets:', cloudSync.getTargets().map(t => t.name).join(', ') || 'none detected');
  } catch (err) {
    console.error('Cloud sync init failed:', err);
  }

  // Initialize DB
  try {
    const historyWatcherModule = await import('./historyWatcher');
    startHandHistoryWatcher = historyWatcherModule.startHandHistoryWatcher;
    const dbData = initDatabase();
    dbUrl = dbData.dbUrl;
    db = dbData.db;
    console.log('Database initialized at:', dbUrl);
  } catch (err) {
    console.error('CRITICAL STARTUP ERROR:', err);
    dialog.showErrorBox('Startup Error', `Failed to initialize.\n${err}`);
  }

  createWindow();

  // Poll active poker windows
  setInterval(() => {
    checkActiveWindows((data: any) => {
      if (win && !win.isDestroyed()) {
        win.webContents.send('active-window-data', data);
      }
      if (data?.bounds) {
        overlayManager.updateOverlay(data.windowTitle, data.bounds);
      }
    });
  }, 1000);

  // ── Watch ALL poker client hand history directories ──
  const sendLog = (msg: string, type: 'info' | 'error') => {
    console.log(`[Watcher] ${msg}`);
    if (win && !win.isDestroyed()) win.webContents.send('app-log', { msg, type });
  };

  // Initialize auto-backup (every 12 hours)
  try {
    autoBackup = new AutoBackupService(sendLog);
    autoBackup.start();
    console.log('Auto-backup service started (12-hour interval)');
  } catch (err) {
    console.error('Auto-backup init failed:', err);
  }

  // ── IPC: Backup controls ──
  ipcMain.handle('backup:runNow', async () => {
    if (!autoBackup) return { success: false, files: [] };
    return autoBackup.runBackup();
  });
  ipcMain.handle('backup:getBackups', () => {
    return autoBackup ? autoBackup.getBackups() : [];
  });
  ipcMain.handle('backup:getDir', () => {
    return autoBackup ? autoBackup.getBackupDir() : '';
  });

  const activePaths = getActiveWatchPaths();
  const watchDirs = activePaths.map(a => a.path);
  const pathToSiteMap = new Map(activePaths.map(a => [a.path, a.site]));

  if (watchDirs.length > 0) {
    console.log(`Watching ${watchDirs.length} hand history directories:`);
    for (const wp of activePaths) console.log(`  [${wp.site}] ${wp.path}`);
    sendLog(`Watching ${watchDirs.length} HH paths: ${activePaths.map(a => a.site).join(', ')}`, 'info');
  } else {
    console.log('No poker client HH directories found — configure in Settings');
    sendLog('No HH paths found. Add paths in Settings > Hand History Paths.', 'error');
  }

  historyWatcher = startHandHistoryWatcher(watchDirs, (newContent: string, detectedSite: string) => {
    // Use the path→site mapping when possible, fall back to auto-detection
    const site = detectedSite !== 'Unknown' ? detectedSite : 'DriveHUD2';
    console.log(`[${site}] New hand data received (${newContent.length} chars)`);

    // 1. Write summary file for AI review
    writeHandSummary(site, newContent);

    // 2. Parse into structured data (multi-format parser)
    const parsedHands = parseHandHistory(newContent, site);

    // 3. Store in local DB
    if (parsedHands.length > 0) {
      try {
        const { sqlite } = require('./db/index');
        const insertHand = sqlite.prepare(`
          INSERT OR IGNORE INTO hands (id, session_id, site, game_type, stakes, timestamp, board, hero_cards, pot_size, won_pot, net_amount)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `);
        const insertAction = sqlite.prepare(`
          INSERT INTO actions (hand_id, player_name, action_type, amount, street)
          VALUES (?, ?, ?, ?, ?)
        `);
        for (const h of parsedHands) {
          try {
            const ts = new Date(h.timestamp).getTime() || Date.now();
            const res = insertHand.run(h.id, null, h.site, h.gameType, h.stakes, ts,
              h.board.join(','), h.heroCards.join(','), Math.round(h.potSize * 100),
              h.heroWon ? 1 : 0, Math.round(h.heroNetAmount * 100));
            if (res.changes > 0) {
              for (const a of h.actions) {
                insertAction.run(h.id, a.playerName, a.type, Math.round((a.amount || 0) * 100), a.street);
              }
            }
          } catch (_e) { /* skip duplicates */ }
        }
        sendLog(`Imported ${parsedHands.length} hands from ${site}`, 'info');
      } catch (err) {
        sendLog(`DB import error: ${err}`, 'error');
      }
    }

    // 4. Sync to cloud
    if (cloudSync) {
      cloudSync.syncHandText(newContent, site, sendLog);
    }

    // 5. Notify renderer
    if (win && !win.isDestroyed()) {
      win.webContents.send('new-hand-history', { site, raw: newContent });
      if (parsedHands.length > 0) {
        win.webContents.send('new-parsed-hand', parsedHands);
      }
    }
  }, sendLog);
});
