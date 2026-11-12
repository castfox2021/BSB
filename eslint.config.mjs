import js from '@eslint/js';
import ts from 'typescript-eslint';
export default ts.config({ignores:['.tooling/**','.python/**','.uv-cache/**','**/.venv/**','**/dist/**','**/.next/**','**/node_modules/**','**/next-env.d.ts']},{files:['**/*.cjs'],languageOptions:{globals:{module:'readonly'}}},{files:['scripts/*.mjs'],languageOptions:{globals:{process:'readonly',console:'readonly',setTimeout:'readonly'}}},js.configs.recommended,...ts.configs.recommended);
