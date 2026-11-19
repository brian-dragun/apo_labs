# backup_labs — original workshop code (do not edit)

A pristine copy of `lab1/`, `lab2/`, `lab3/` and `shared/` exactly as published in
[achankra/apo_labs](https://github.com/achankra/apo_labs), kept so any lab can be reset
to its starting state.

**Don't edit or run anything in this folder.** Work in the real `lab*/` folders and restore from here:

```bash
bash restore-lab.sh lab1 --dry-run        # see what you've changed
bash restore-lab.sh lab1 gate_starter.py  # reset one file
```

See "Restoring a lab to the original code" in the main [README](../README.md#restoring-a-lab-to-the-original-code).

**Refreshing this copy** after the instructor publishes updates (overwrites the backup with upstream's version):

```bash
git fetch upstream
for d in lab1 lab2 lab3 shared; do rm -rf "backup_labs/$d"; git archive upstream/main "$d" | tar -x -C backup_labs; done
```
