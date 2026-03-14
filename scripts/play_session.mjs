// Kaetram gameplay session script
import { chromium } from 'playwright';
import { writeFileSync } from 'fs';

const SCREENSHOT = '/home/patnir41/projects/kaetram-agent/state/screenshot.png';

async function run() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
  const page = await context.newPage();

  console.log('=== PHASE 1: LOGIN ===');
  await page.goto('http://localhost:9000');
  await page.waitForTimeout(3000);
  await page.locator('#login-name-input').fill('ClaudeBot');
  await page.locator('#login-password-input').fill('password123');
  await page.getByRole('button', { name: 'Login' }).click();
  await page.waitForTimeout(8000);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(1000);
  await page.screenshot({ path: SCREENSHOT, type: 'png' });
  console.log('Logged in, screenshot saved');

  // Walk south to exit tutorial room
  await page.keyboard.down('s');
  await page.waitForTimeout(4000);
  await page.keyboard.up('s');
  await page.waitForTimeout(1000);
  await page.screenshot({ path: SCREENSHOT, type: 'png' });
  console.log('Walked south');

  // Keep walking south
  await page.keyboard.down('s');
  await page.waitForTimeout(3000);
  await page.keyboard.up('s');
  await page.waitForTimeout(1000);
  await page.screenshot({ path: SCREENSHOT, type: 'png' });
  console.log('Continued south - should be in overworld');

  await browser.close();
}

run().catch(console.error);
