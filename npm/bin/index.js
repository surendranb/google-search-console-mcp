#!/usr/bin/env node

/**
 * Google Search Console MCP Server NPX Wrapper
 * Spawns the python package via uvx with full stdio passthrough for MCP hosts.
 */

const { spawn } = require('child_process');

const args = ['--from', 'google-search-console-mcp', 'google-search-console-mcp', ...process.argv.slice(2)];

const child = spawn('uvx', args, {
  stdio: 'inherit',
  shell: process.platform === 'win32'
});

child.on('error', (err) => {
  if (err.code === 'ENOENT') {
    console.error('[google-search-console-mcp Error] "uvx" command not found.');
    console.error('Please install uv (https://astral.sh/uv) or install directly via pip: pip install google-search-console-mcp');
  } else {
    console.error('[google-search-console-mcp Error] Failed to start server process:', err.message);
  }
  process.exit(1);
});

child.on('close', (code) => {
  process.exit(code || 0);
});
