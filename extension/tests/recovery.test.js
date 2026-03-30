const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

function loadRecoveryModule() {
  const modulePath = path.resolve(__dirname, '../out/shared/recovery.js');
  delete require.cache[modulePath];
  return require(modulePath);
}

test('recoveryFromPayload reads structured recovery metadata', () => {
  const { recoveryFromPayload } = loadRecoveryModule();

  assert.deepEqual(
    recoveryFromPayload({
      recovery: {
        reason: 'lease-conflict',
        summary: 'Another session holds the lease.',
        suggestions: ['Refresh the notebook.'],
      },
    }),
    {
      reason: 'lease-conflict',
      summary: 'Another session holds the lease.',
      suggestions: ['Refresh the notebook.'],
    },
  );
});

test('stalePreviewServerRecovery suggests a restart path', () => {
  const { stalePreviewServerRecovery } = loadRecoveryModule();

  const recovery = stalePreviewServerRecovery();
  assert.equal(recovery.reason, 'stale-preview-server');
  assert.match(recovery.summary, /missing the notebook API routes/i);
  assert.equal(recovery.commands[0].label, 'Restart preview');
});
