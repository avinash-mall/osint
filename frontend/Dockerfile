# Pinned to the 22.22 minor (matches the committed package-lock.json toolchain).
# `npm ci` installs exactly from the lockfile and fails if it drifts from
# package.json — reproducible builds for the air-gap bundle.
FROM node:22.22-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

RUN npm run build

CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "3000"]
