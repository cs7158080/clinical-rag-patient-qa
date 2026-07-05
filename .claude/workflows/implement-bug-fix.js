export const meta = {
  name: 'implement-bug-fix',
  description: 'Implement a planned bug fix, run tests, and report results',
  phases: [
    { title: 'Read plan & source', detail: 'Read the planning document and all affected source files' },
    { title: 'Implement fix', detail: 'Apply the changes described in the plan' },
    { title: 'Run tests', detail: 'Run the full test suite and verify the fix' },
  ],
}

// args: { bug_id, plan_path, project_root }
// Returns: { success: boolean, test_output: string, files_changed: string[] }

const RESULT_SCHEMA = {
  type: 'object',
  properties: {
    success: { type: 'boolean' },
    test_output: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    error: { type: 'string' },
  },
  required: ['success', 'test_output', 'files_changed'],
}

phase('Read plan & source')
const planAndContext = await agent(
  `Read the planning document at "${args.plan_path}" in full.
  Then read every source file listed in the plan's "Files to Modify" section.
  Also read the test files at "${args.project_root}/tests/" to understand what assertions exist.
  Return a JSON object with:
  - plan_summary: one-paragraph summary of what needs to change
  - files_to_edit: array of {file_path, description_of_change}
  - test_command: exact shell command to run tests`,
  {
    label: 'read-plan',
    schema: {
      type: 'object',
      properties: {
        plan_summary: { type: 'string' },
        files_to_edit: {
          type: 'array',
          items: {
            type: 'object',
            properties: { file_path: { type: 'string' }, description_of_change: { type: 'string' } },
            required: ['file_path', 'description_of_change'],
          },
        },
        test_command: { type: 'string' },
      },
      required: ['plan_summary', 'files_to_edit', 'test_command'],
    },
  }
)

log(`Plan loaded: ${planAndContext.plan_summary}`)
log(`Files to edit: ${planAndContext.files_to_edit.map(f => f.file_path).join(', ')}`)

phase('Implement fix')
const implementation = await agent(
  `You are implementing the bug fix described in the plan at "${args.plan_path}".

  Bug ID: ${args.bug_id}
  Project root: ${args.project_root}

  The plan requires these changes:
  ${planAndContext.files_to_edit.map(f => `- ${f.file_path}: ${f.description_of_change}`).join('\n')}

  IMPORTANT RULES:
  1. Read each file before editing it
  2. Make ONLY the changes described in the plan — no refactoring, no extras
  3. Preserve all existing code that is not part of the fix
  4. After editing each file, verify the change looks correct
  5. Return a list of all files you actually changed`,
  {
    label: 'apply-fix',
    schema: {
      type: 'object',
      properties: {
        files_changed: { type: 'array', items: { type: 'string' } },
        summary: { type: 'string' },
      },
      required: ['files_changed', 'summary'],
    },
  }
)

log(`Fix applied to: ${implementation.files_changed.join(', ')}`)

phase('Run tests')
const testResult = await agent(
  `Run the test suite for the project at "${args.project_root}".

  Test command: ${planAndContext.test_command}

  Run the command using Bash. Capture ALL output.
  Determine if the tests passed (exit code 0 and no FAILED lines) or failed.
  Return the full test output and whether the tests passed.`,
  {
    label: 'run-tests',
    schema: RESULT_SCHEMA,
  }
)

if (testResult.success) {
  log(`Tests PASSED for ${args.bug_id}`)
} else {
  log(`Tests FAILED for ${args.bug_id} — fix may need revision`)
}

return {
  success: testResult.success,
  test_output: testResult.test_output,
  files_changed: implementation.files_changed,
  error: testResult.error || null,
}
