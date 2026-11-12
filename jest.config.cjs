module.exports = { testEnvironment: 'node', testMatch: ['**/*.spec.ts'], transform: { '^.+\\.tsx?$': ['ts-jest', { tsconfig: 'apps/api/tsconfig.json' }] } };
