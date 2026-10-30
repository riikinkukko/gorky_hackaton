#!/usr/bin/env bash
# Первичная настройка VPS (Ubuntu 22.04/24.04). Запуск от root:
#   curl -fsSL https://raw.githubusercontent.com/riikinkukko/gorky_hackaton/main/deploy/setup-vps.sh | bash
# или после клона: sudo bash deploy/setup-vps.sh
set -euo pipefail

REPO="${REPO:-https://github.com/riikinkukko/gorky_hackaton.git}"
DIR="${DIR:-/opt/gorky_code}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"

echo "== Docker =="
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
apt-get update -y && apt-get install -y git make curl ufw

echo "== Пользователь для деплоя: $DEPLOY_USER =="
id "$DEPLOY_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$DEPLOY_USER"
usermod -aG docker "$DEPLOY_USER"
mkdir -p "/home/$DEPLOY_USER/.ssh" && chmod 700 "/home/$DEPLOY_USER/.ssh"
touch "/home/$DEPLOY_USER/.ssh/authorized_keys" && chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"

echo "== Файрвол: 22, 80, 443 =="
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "== Код в $DIR =="
if [ ! -d "$DIR/.git" ]; then
  git clone "$REPO" "$DIR"
fi
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DIR"
[ -f "$DIR/.env" ] || cp "$DIR/.env.example" "$DIR/.env"

cat <<EOF

Готово. Дальше:
  1) Впишите DOMAIN и ACME_EMAIL в $DIR/.env (нет домена — используйте $(curl -fsS ifconfig.me 2>/dev/null | tr . -).sslip.io)
     и смените POSTGRES_PASSWORD.
  2) sudo -u $DEPLOY_USER make -C $DIR prod-up
  3) make -C $DIR smoke URL=https://<DOMAIN>
  4) Для автодеплоя добавьте публичный ключ GitHub Actions в /home/$DEPLOY_USER/.ssh/authorized_keys
EOF
