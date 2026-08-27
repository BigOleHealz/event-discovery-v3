FROM node:24-alpine

WORKDIR /app

RUN chown node:node /app

USER node

COPY --chown=node:node package.json package-lock.json ./
RUN npm ci

COPY --chown=node:node --chmod=755 entrypoint.sh ./entrypoint.sh
COPY --chown=node:node . .

EXPOSE 3000

ENTRYPOINT ["/bin/sh", "./entrypoint.sh"]
CMD ["npm", "run", "preview"]
