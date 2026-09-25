import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'test',
  testMatch: '*.spec.ts',
  timeout: 45_000,
  use: {
    baseURL: 'http://127.0.0.1:4321',
    launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] },
  },
  webServer: {
    command: 'python3 -m http.server 4321 --bind 127.0.0.1 -d dist',
    url: 'http://127.0.0.1:4321/',
    reuseExistingServer: true,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
