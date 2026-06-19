import { BrowserWindow, screen } from 'electron';
import path from 'path';
import { ELECTRON_ROOT } from './electronPaths';

interface WindowBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export class OverlayManager {
  private overlayWindow: BrowserWindow | null = null;
  private currentTargetTitle: string | null = null;

  constructor() {}

  public updateOverlay(targetTitle: string, bounds: WindowBounds) {
    if (!this.overlayWindow) {
      this.createOverlay(bounds);
    } else {
      // If we switched to a different poker table, or just moved the existing one
      // We update the position.
      // Optimization: Check if bounds actually changed to avoid jitter
      const currentBounds = this.overlayWindow.getBounds();
      if (
        currentBounds.x !== bounds.x ||
        currentBounds.y !== bounds.y ||
        currentBounds.width !== bounds.width ||
        currentBounds.height !== bounds.height
      ) {
        this.overlayWindow.setBounds(bounds);
      }
    }
    
    this.currentTargetTitle = targetTitle;
    
    // Pass the target title to the renderer so it knows which stats to show
    this.overlayWindow?.webContents.send('overlay-target', { title: targetTitle });
  }

  private createOverlay(bounds: WindowBounds) {
    this.overlayWindow = new BrowserWindow({
      x: bounds.x,
      y: bounds.y,
      width: bounds.width,
      height: bounds.height,
      transparent: true,
      frame: false,
      resizable: false,
      hasShadow: false,
      alwaysOnTop: true,
      skipTaskbar: true,
      webPreferences: {
        nodeIntegration: true,
        contextIsolation: true,
        preload: path.join(ELECTRON_ROOT, 'preload.js'),
      },
      focusable: false, // Important so it doesn't steal focus from the poker table
    });

    // Make it click-through -> events fall through to the poker table
    this.overlayWindow.setIgnoreMouseEvents(true, { forward: true });

    // Load the special overlay route in the React app
    // In dev: data is served from localhost
    // In prod: file://.../index.html
    if (process.env.VITE_DEV_SERVER_URL) {
      this.overlayWindow.loadURL(`${process.env.VITE_DEV_SERVER_URL}#/overlay`);
    } else {
      this.overlayWindow.loadFile(path.join(process.env.DIST!, 'index.html'), { hash: 'overlay' });
    }

    this.overlayWindow.on('closed', () => {
      this.overlayWindow = null;
    });

    // Handle mouse events if we want interactive elements (advanced)
    // To make specific elements clickable, we'd need ipc messages from renderer
    // asking to temporarily disable ignoreMouseEvents. 
    // For now, full click-through is safer.
  }

  public hideOverlay() {
    if (this.overlayWindow && !this.overlayWindow.isDestroyed()) {
        this.overlayWindow.hide();
    }
  }

  public showOverlay() {
    if (this.overlayWindow && !this.overlayWindow.isDestroyed()) {
        this.overlayWindow.show();
    }
  }

  public destroy() {
    if (this.overlayWindow) {
      this.overlayWindow.close();
      this.overlayWindow = null;
    }
  }
}

export const overlayManager = new OverlayManager();
