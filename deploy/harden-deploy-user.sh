#!/usr/bin/env bash
# SEC-04/SEC-15: перевести автодеплой с root на пользователя deploy с одной разрешённой командой.
# Запуск на сервере от root, ДЕРЖА ОТКРЫТОЙ ВТОРУЮ root-сессию:  bash /opt/gorky_code/deploy/harden-deploy-user.sh
# Ничего не удаляет у root, если ключ Actions там единственный; любые сомнения — остановка без изменений.
set -euo pipefail
key=${KEY:-/root/.ssh/gh_actions.pub}
ak=${ROOT_AK:-/root/.ssh/authorized_keys}
dak=${DEPLOY_AK:-/home/deploy/.ssh/authorized_keys}
die() { echo "ОСТАНОВКА: $*" >&2; exit 1; }

# ---------- 0. проверки до любых изменений ----------
[ "$(id -u)" -eq 0 ] || die "запускать от root"
[ -s "$key" ] || die "нет $key — положите туда ПУБЛИЧНЫЙ ключ Actions (одна строка .pub)"
[ "$(grep -cE '[^[:space:]]' "$key")" -eq 1 ] || die "$key: нужна ровно одна строка с ключом"
read -r ktype kbody _ < <(tr -d '\r' < "$key")
case "$ktype" in ssh-ed25519|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|ssh-rsa) ;;
  *) die "$key: первая колонка «$ktype» — не тип ключа (нужно «ssh-ed25519 AAAA…»)";; esac
[ "${#kbody}" -ge 60 ] || die "$key: тело ключа слишком короткое"
printf '%s %s\n' "$ktype" "$kbody" | ssh-keygen -lf /dev/stdin >/dev/null 2>&1 || die "$key: ssh-keygen не принимает ключ"

# ---------- 1. пользователь deploy и единственная команда ----------
id deploy >/dev/null 2>&1 || adduser --disabled-password --gecos "" deploy
gpasswd -d deploy docker 2>/dev/null || true          # без прямого доступа к docker
install -o root -g root -m 0755 /opt/gorky_code/deploy/remote-deploy.sh /usr/local/sbin/gorky-deploy
tmp=$(mktemp)
echo 'deploy ALL=(root) NOPASSWD: /usr/local/sbin/gorky-deploy ""' > "$tmp"   # без аргументов
visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; die "sudoers не прошёл проверку"; }
install -o root -g root -m 0440 "$tmp" /etc/sudoers.d/gorky-deploy && rm -f "$tmp"

# ---------- 2. ключ Actions → deploy (restrict + forced command) ----------
install -d -o deploy -g deploy -m 0700 "$(dirname "$dak")"
umask 077
printf 'restrict,command="sudo -n /usr/local/sbin/gorky-deploy" %s %s gha-deploy\n' "$ktype" "$kbody" > "$dak.new"
chown deploy:deploy "$dak.new" && mv "$dak.new" "$dak"

# ---------- 3. убрать ключ Actions у root — только если у root останутся другие ключи ----------
if [ -f "$ak" ] && grep -qF -- "$kbody" "$ak"; then
  others=$(grep -vF -- "$kbody" "$ak" | grep -cE '^[[:space:]]*[^#[:space:]]' || true)
  if [ "$others" -ge 1 ]; then
    cp -p "$ak" "$ak.bak.$(date +%Y%m%d%H%M%S)"
    grep -vF -- "$kbody" "$ak" > "$ak.new"
    [ "$(grep -cE '^[[:space:]]*[^#[:space:]]' "$ak.new")" -eq "$others" ] || die "проверка не сошлась, $ak не изменён"
    chmod 0600 "$ak.new" && mv "$ak.new" "$ak"
    echo "Ключ Actions убран у root; осталось ключей: $others; копия — $ak.bak.*"
  else
    echo "ВНИМАНИЕ: ключ Actions — единственный ключ root, у root ничего не удаляю."
    echo "  Добавьте root свой личный ключ и запустите скрипт ещё раз, чтобы убрать ключ Actions у root."
  fi
else
  echo "У root ключа Actions нет — там ничего не меняю"
fi

# ---------- 4. права на код и секреты ----------
chown -R root:root /opt/gorky_code
[ -f /opt/gorky_code/.env ] && chmod 600 /opt/gorky_code/.env

echo
echo "Готово. В GitHub → Settings → Secrets → Actions поменяйте:"
echo "  VPS_USER = deploy"
echo "  VPS_FINGERPRINT = $(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256 | awk '{print $2}')"
echo "Проверка с другой машины: ssh -i <ключ Actions> deploy@сервер  → выполнит только деплой."
