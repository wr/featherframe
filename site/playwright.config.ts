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
    // test/serve.mjs: dist/ with a real listen backlog (python's http.server
    // reset connections under parallel workers). Its stderr stays visible.
    command: 'node test/serve.mjs',
    url: 'http://127.0.0.1:4321/',
    reuseExistingServer: true,
    stdout: 'ignore',
    stderr: 'pipe',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
