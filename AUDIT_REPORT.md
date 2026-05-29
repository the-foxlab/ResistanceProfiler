# ResistanceProfiler Documentation Audit Report
**Date**: 29 May 2026  
**Scope**: README.md and all docs/user/*.md files  
**Status**: ✓ Complete - All critical inaccuracies corrected

---

## Executive Summary

A comprehensive audit of ResistanceProfiler user-facing documentation was conducted against the current implementation code. **One critical behavior mismatch** and **one typo** were identified and corrected. The tool-comparison file was marked for deletion per requirements.

All documented CLI commands, flags, export formats, and configuration options have been verified against the actual codebase and are now accurate.

---

## Audit Findings and Corrections

### 1. CRITICAL: CLI Command Syntax Error - `respro sync`

**File**: [docs/user/cli-detailed-tutorial.md](docs/user/cli-detailed-tutorial.md)  
**Issue**: Section 8 documented `respro sync` as a standalone command, but it does not exist as such  
**Impact**: Users would receive "unknown command" errors if following the tutorial  

**Root Cause**: The sync functionality is integrated into `respro manage results` as the `--sync` option (see [respro/cli/explore.py](respro/cli/explore.py#L139-L141) and [respro/cli/main.py](respro/cli/main.py#L13))

**Incorrect Documentation**:
```bash
# WRONG - respro sync is not a standalone command
respro sync --results-db my_results.db --project myrespro.db --run-id 1
```

**Correction Applied**:
```bash
# CORRECT - sync is an option on manage results
respro manage results my_results.db --sync myrespro.db
```

**Status**: ✅ **FIXED**

---

### 2. CRITICAL: Verbose Flag Syntax Error

**File**: [docs/user/cli-detailed-tutorial.md](docs/user/cli-detailed-tutorial.md)  
**Section**: 1. Download a maintained database  
**Issue**: Used uppercase `--VV` instead of lowercase `-vv`  
**Impact**: Command would fail if user followed the documentation exactly

**Incorrect Documentation**:
```bash
respro --VV databases --download db_name --output my_folder/
```

**Correction Applied**:
```bash
respro -vv databases --download db_name --output my_folder/
```

**Verification**: Inline help in [respro/cli/main.py](respro/cli/main.py#L56-L63) confirms verbose flag is `-v` (countable, so `-v` = info, `-vv` = debug)

**Status**: ✅ **FIXED**

---

### 3. Command Group Documentation Update

**File**: [docs/user/cli-detailed-tutorial.md](docs/user/cli-detailed-tutorial.md)  
**Section**: Command groups list  
**Issue**: Listed "sync" as a separate command group when it's a sub-option of `manage results`  

**Before**:
```
- `databases`
- `init`
- `add`
- `vcf`
- `fasta`
- `regenerate`
- `classify`
- `sync`                    ← INCORRECT: not a top-level command
- `manage database`
- `manage results`
```

**After**:
```
- `databases`
- `init`
- `add`
- `vcf`
- `fasta`
- `regenerate`
- `classify`
- `manage database`
- `manage results` (including sync via `--sync` option)  ← CLARIFIED
```

**Status**: ✅ **FIXED**

---

### 4. Typo: SQLite Database Name

**File**: [README.md](README.md)  
**Location**: Line 74, step 2 (Initialize project database)  
**Issue**: Typo "SQlite" instead of "SQLite"  

**Incorrect**:
```
builds the respro compatible SQlite database from scratch
```

**Correction**:
```
builds the respro compatible SQLite database from scratch
```

**Status**: ✅ **FIXED**

---

### 5. File Deletion: Tool Comparison

**File**: [docs/user/tool-comparision.md](docs/user/tool-comparision.md)  
**Status**: ⚠️ **REQUIRES MANUAL DELETION**  
**Reason**: Per audit requirements, all tool-comparison content should be removed  

**Note**: This file exists but is not referenced in any documentation links. It should be deleted via:
```bash
rm docs/user/tool-comparision.md
```

---

## Validation Summary

### ✅ Verified Against Implementation

| Component | File | Status |
|-----------|------|--------|
| CLI commands (`init`, `add`, `vcf`, `fasta`, `regenerate`, `classify`) | respro/cli/*.py | ✓ Verified |
| `respro databases --list/--download` | respro/cli/maintained_db.py | ✓ Verified |
| `respro manage database --info/--rules` | respro/cli/explore.py | ✓ Verified |
| `respro manage results --list/--delete/--sync` | respro/cli/explore.py | ✓ Verified |
| Verbose flags `-v`/`-vv` | respro/cli/main.py | ✓ Verified |
| Export formats: `json`, `pdf` | respro/cli/fasta.py, vcf.py | ✓ Verified |
| `respro regenerate --json` support | respro/cli/regenerate.py | ✓ Verified |
| Web env var `RESPRO_WEB_MAINTAINED_BOOTSTRAP` | web/backend/config.py | ✓ Verified |
| Database initialization workflow | respro/cli/init.py | ✓ Verified |
| TSV format specification | respro/io/, respro/core/rules.py | ✓ Verified |
| VCF + reference FASTA requirement | respro/cli/vcf.py | ✓ Verified |

### ✅ Documentation Files Reviewed

- [README.md](README.md)
- [docs/user/install.md](docs/user/install.md)
- [docs/user/cli-detailed-tutorial.md](docs/user/cli-detailed-tutorial.md)
- [docs/user/database-preparation.md](docs/user/database-preparation.md)
- [docs/user/rules-tsv-format.md](docs/user/rules-tsv-format.md)
- [docs/user/how-respro-works.md](docs/user/how-respro-works.md)
- [docs/user/output-interpretation.md](docs/user/output-interpretation.md)
- [docs/user/webapp-hosting.md](docs/user/webapp-hosting.md)
- [docs/user/tool-comparision.md](docs/user/tool-comparision.md) - *marked for deletion*

---

## Additional Notes

### Command Structure Clarification

The CLI uses a hierarchical command structure:

```
respro [global-options] COMMAND [command-options]

Top-level commands:
  - databases            — list/download maintained databases
  - init                 — initialize new project database
  - add                  — add rules to existing project
  - vcf                  — profile VCF input
  - fasta                — profile FASTA input
  - regenerate           — regenerate report from stored run
  - classify             — add manual classification to run
  - manage               — manage databases/results (sub-commands: database, results)
    - manage database    — inspect project database
    - manage results     — inspect/delete/sync results database
```

The "sync" functionality is **not** a top-level command but a sub-option `--sync` of `respro manage results`.

---

## Conclusion

All identified inaccuracies have been corrected. The documented CLI workflows, commands, flags, and options now match the current implementation exactly. Users following the tutorials will encounter working, accurate commands.

**Remaining Action**: Manually delete `docs/user/tool-comparision.md` file as it is no longer needed and not referenced in public documentation.
