# Testing Notes

## Local Temp Dirs (Windows-safe)

- Tests that need temporary filesystem state should prefer `tests._temp_utils.repo_temp_dir`.
- This helper creates directories under `./.tmp_tests_local` instead of OS temp.
- On some Windows setups, this avoids intermittent permission failures seen with `tempfile.TemporaryDirectory()`.

Cleanup command:

```powershell
if (Test-Path .tmp_tests_local) { Remove-Item -Recurse -Force .tmp_tests_local }
```
