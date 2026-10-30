# Реплики стенда → репозиторий логов

Раз в час `deploy/ship-turns.sh` берёт из логов API строки `turn session` (фраза, intent, ответ),
маскирует почты и длинные числа и пушит в **отдельный приватный** репозиторий
`riikinkukko/gorky_logs` — по файлу на день: `turns/YYYY-MM-DD.log`. Чат 0 и утренняя проверка
читают их оттуда и прогоняют через оракулы QA.

Основной репозиторий после сдачи станет публичным, логов в нём нет и не будет.

## Установка (один раз, на сервере под root)

1. На GitHub создать **приватный** пустой репозиторий `gorky_logs` (с README, чтобы была ветка main).
2. Ключ только для него:
   ```
   ssh-keygen -t ed25519 -N "" -C gorky-logs -f /root/.ssh/gorky_logs
   cat /root/.ssh/gorky_logs.pub
   ```
   GitHub → gorky_logs → Settings → Deploy keys → Add: вставить ключ, **Allow write access** ✔.
3. Клон и cron:
   ```
   GIT_SSH_COMMAND="ssh -i /root/.ssh/gorky_logs -o IdentitiesOnly=yes" \
     git clone git@github.com:riikinkukko/gorky_logs.git /opt/gorky_logs
   git -C /opt/gorky_logs config core.sshCommand "ssh -i /root/.ssh/gorky_logs -o IdentitiesOnly=yes"
   chmod 700 /opt/gorky_logs
   echo '7 * * * * root /opt/gorky_code/deploy/ship-turns.sh >/dev/null 2>&1' > /etc/cron.d/gorky-turns
   /opt/gorky_code/deploy/ship-turns.sh && git -C /opt/gorky_logs log --oneline -1
   ```
   Последняя команда сразу отправляет первые реплики и показывает коммит.

Отключить: `rm /etc/cron.d/gorky-turns`. Ключ отозвать — удалить его в Deploy keys.
