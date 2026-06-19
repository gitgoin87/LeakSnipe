import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron'
import renderer from 'vite-plugin-electron-renderer'

const electronExternals = [
  'electron',
  'better-sqlite3',
  'get-windows',
  'drizzle-orm',
  'drizzle-orm/better-sqlite3',
]

function electronBuildOptions() {
  return {
    build: {
      rollupOptions: {
        external: electronExternals,
        output: {
          format: 'cjs' as const,
        },
      },
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [
    react(),
    electron([
      {
        entry: 'electron/main.ts',
        vite: electronBuildOptions(),
      },
      {
        entry: 'electron/preload.ts',
        vite: {
          build: {
            rollupOptions: {
              external: ['electron'],
              output: {
                format: 'cjs' as const,
              },
            },
          },
        },
        onstart(options) {
          options.reload()
        },
      },
    ]),
    renderer({
      resolve: {
        'better-sqlite3': { type: 'cjs' },
        'get-windows': { type: 'cjs' },
      },
    }),
  ],
})
