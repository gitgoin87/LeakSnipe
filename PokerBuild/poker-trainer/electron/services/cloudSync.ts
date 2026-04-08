// Cloud Sync Module
// Syncs hand histories to Google Drive, OneDrive, and network paths

import fs from 'fs';
import path from 'path';
import { app } from 'electron';

export interface SyncTarget {
  id: string;
  name: string;
  type: 'google-drive' | 'onedrive' | 'dropbox' | 'network';
  basePath: string;
  enabled: boolean;
  lastSync?: string;
  fileCount?: number;
}

export interface SyncResult {
  target: string;
  success: boolean;
  filesWritten: number;
  error?: string;
}

const DEFAULT_SUBFOLDER = 'PokerHandHistories';

export class CloudSyncManager {
  private targets: SyncTarget[] = [];
  private settingsPath: string;

  constructor() {
    this.settingsPath = path.join(app.getPath('userData'), 'cloud-sync-settings.json');
    this.loadSettings();
  }

  private loadSettings() {
    try {
      if (fs.existsSync(this.settingsPath)) {
        const data = JSON.parse(fs.readFileSync(this.settingsPath, 'utf8'));
        this.targets = data.targets || [];
        return;
      }
    } catch (_e) { /* use defaults */ }

    // Auto-detect defaults
    this.targets = this.detectCloudFolders();
    this.saveSettings();
  }

  private saveSettings() {
    try {
      fs.writeFileSync(this.settingsPath, JSON.stringify({ targets: this.targets }, null, 2), 'utf8');
    } catch (e) {
      console.error('[CloudSync] Failed to save settings:', e);
    }
  }

  detectCloudFolders(): SyncTarget[] {
    const userProfile = process.env.USERPROFILE || 'C:\\Users\\mfane';
    const detected: SyncTarget[] = [];

    // Google Drive — check Windows sync folder mounts
    const googlePaths = [
      path.join(userProfile, 'My Drive (maurofanellijr@gmail.com)'),
      path.join(userProfile, 'My Drive (johndawalka87@gmail.com)'),
      path.join(userProfile, 'My Drive'),
      path.join(userProfile, 'Google Drive'),
      path.join(userProfile, 'GoogleDrive'),
      'G:\\My Drive',
    ];
    for (const gp of googlePaths) {
      if (fs.existsSync(gp)) {
        detected.push({
          id: 'google-drive', name: 'Google Drive',
          type: 'google-drive', basePath: path.join(gp, DEFAULT_SUBFOLDER), enabled: true
        });
        break;
      }
    }

    // OneDrive — Windows locations
    const oneDrivePaths = [
      path.join(userProfile, 'OneDrive'),
      path.join(userProfile, 'OneDrive - CSCU'),
      path.join(userProfile, 'OneDrive-CSCU'),
      path.join(userProfile, 'OneDrive - Connecticut State Community College'),
    ];
    for (const op of oneDrivePaths) {
      if (fs.existsSync(op)) {
        detected.push({
          id: 'onedrive',
          name: op.includes('CSCU') || op.includes('Connecticut State Community College') ? 'OneDrive (CSCU)' : 'OneDrive',
          type: 'onedrive', basePath: path.join(op, DEFAULT_SUBFOLDER), enabled: true
        });
        break;
      }
    }

    // Dropbox — common desktop sync locations
    const dropboxPaths = [
      path.join(userProfile, 'Dropbox'),
      path.join(userProfile, 'Dropbox (Personal)'),
      path.join(userProfile, 'Dropbox (CSCU)'),
    ];
    for (const dp of dropboxPaths) {
      if (fs.existsSync(dp)) {
        detected.push({
          id: 'dropbox',
          name: dp.includes('CSCU') ? 'Dropbox (CSCU)' : 'Dropbox',
          type: 'dropbox',
          basePath: path.join(dp, DEFAULT_SUBFOLDER),
          enabled: true
        });
        break;
      }
    }

    return detected;
  }

  getTargets(): SyncTarget[] {
    return this.targets;
  }

  addTarget(target: Omit<SyncTarget, 'id'>): SyncTarget {
    const id = `custom-${Date.now()}`;
    const newTarget: SyncTarget = { ...target, id };
    this.targets.push(newTarget);
    this.saveSettings();
    return newTarget;
  }

  updateTarget(id: string, updates: Partial<SyncTarget>) {
    const idx = this.targets.findIndex(t => t.id === id);
    if (idx >= 0) {
      this.targets[idx] = { ...this.targets[idx], ...updates };
      this.saveSettings();
    }
  }

  removeTarget(id: string) {
    this.targets = this.targets.filter(t => t.id !== id);
    this.saveSettings();
  }

  // Sync a single hand history file to all enabled targets
  syncFile(filePath: string, onLog?: (msg: string, type: 'info' | 'error') => void): SyncResult[] {
    const results: SyncResult[] = [];
    const fileName = path.basename(filePath);

    for (const target of this.targets.filter(t => t.enabled)) {
      try {
        if (!fs.existsSync(target.basePath)) {
          fs.mkdirSync(target.basePath, { recursive: true });
        }
        const destPath = path.join(target.basePath, fileName);
        fs.copyFileSync(filePath, destPath);
        target.lastSync = new Date().toISOString();
        target.fileCount = (target.fileCount || 0) + 1;
        results.push({ target: target.name, success: true, filesWritten: 1 });
        onLog?.(`[CloudSync] ✓ Synced ${fileName} to ${target.name}`, 'info');
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        results.push({ target: target.name, success: false, filesWritten: 0, error: errMsg });
        onLog?.(`[CloudSync] ✗ Failed to sync to ${target.name}: ${errMsg}`, 'error');
      }
    }
    this.saveSettings();
    return results;
  }

  // Sync a raw hand text (write it to a timestamped file in each target)
  syncHandText(handText: string, site: string, onLog?: (msg: string, type: 'info' | 'error') => void): SyncResult[] {
    const results: SyncResult[] = [];
    const now = new Date();
    const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
    const fileName = `HH_${site}_${dateStr}.txt`;

    for (const target of this.targets.filter(t => t.enabled)) {
      try {
        if (!fs.existsSync(target.basePath)) {
          fs.mkdirSync(target.basePath, { recursive: true });
        }
        const destPath = path.join(target.basePath, fileName);
        // Append to daily file
        fs.appendFileSync(destPath, handText + '\n\n', 'utf8');
        target.lastSync = now.toISOString();
        results.push({ target: target.name, success: true, filesWritten: 1 });
        onLog?.(`[CloudSync] ✓ Appended hand to ${target.name}/${fileName}`, 'info');
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        results.push({ target: target.name, success: false, filesWritten: 0, error: errMsg });
        onLog?.(`[CloudSync] ✗ Failed: ${errMsg}`, 'error');
      }
    }
    this.saveSettings();
    return results;
  }

  // Bulk sync all files from a directory
  syncDirectory(dirPath: string, onLog?: (msg: string, type: 'info' | 'error') => void): SyncResult[] {
    const allResults: SyncResult[] = [];
    if (!fs.existsSync(dirPath)) return allResults;

    const files = fs.readdirSync(dirPath).filter(f => f.endsWith('.txt') || f.endsWith('.xml'));
    for (const file of files) {
      const results = this.syncFile(path.join(dirPath, file), onLog);
      allResults.push(...results);
    }
    return allResults;
  }
}
