# Project update requirements

This project is built by Nexaibyte Ltd. Follow the user's latest instructions and preserve staged human review and editable production controls.

- Read `TODO.md` and `UPDATE_WORKFLOW.md` before making project changes.
- Review and update the living TODO after each feature/fix. Distinguish implemented source from live build-machine validation and future proposals. Add discovered tasks and preserve unresolved work.
- Every delivery response must include applicable build/update commands, migration/configuration requirements (or explicitly none), and a suggested Git commit message.
- State what changed, relevant validation and limitations, and whether changes were actually committed, pushed or deployed.
- Default gateway rebuild: run from `db` using `docker compose -f docker-compose.yml -f docker-compose.automation.yml up -d --build --force-recreate gateway`. Account for other services only when the change requires them.
- Keep creative prompt rules editable or conditional where practical; preserve current defaults, model limits, project/file safety and review controls.
- Never treat a proposed TODO task as authorization for paid generation, deployment, automatic permanent deletion or messaging others. Commit/push only when authorized by the user.
