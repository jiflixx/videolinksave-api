# VideoLinkSave — serveur vidéo multi-plateforme

État : code préparé, pas encore hébergé. Aucun téléchargement social n’est garanti avant un test réel depuis l’hébergement choisi.

## Plateformes

- Facebook : vidéos publiques, Reels et liens fb.watch.
- Instagram : publications vidéo et Reels publics.
- TikTok : vidéos publiques ordinaires et liens courts. TikTok Minis exclu.
- X : publications publiques contenant une vidéo.
- Snapchat : Spotlight public uniquement, pas les messages privés.
- NetShort : adaptateur pour épisodes non verrouillés.

yt-dlp 2026.10.26 fournit les extracteurs sociaux sélectionnés, y compris SnapchatSpotlight. Les sites peuvent changer et refuser un serveur. Aucun cookie ni identifiant utilisateur n’est collecté. Aucun déverrouillage payant ou DRM n’est fourni.

## Déploiement

Nécessite un hébergeur Docker/Linux avec Python et FFmpeg, sortie HTTPS et stockage temporaire. Le serveur ne fonctionne pas dans un thème Blogger ou un Cloudflare Worker JavaScript.

```
docker build -t videolinksave-api .
docker run --rm -p 8000:8000 -e ALLOWED_ORIGINS=https://videolinksave.blogspot.com videolinksave-api
```

En production, publier derrière HTTPS (fourni par votre hébergeur). Ne placer aucun secret dans le thème Blogger. Configurer les limites de trafic et de stockage de l’hébergeur avant une ouverture large.

Vérification : GET /health. Il indique que le processus est actif ; il ne prouve pas que chaque plateforme fonctionne.

Dans le XML Blogger, remplacer `const API_BASE='';` par l’URL HTTPS de ce serveur. Importer le thème puis tester un lien public réel par plateforme. Les réponses d’échec restent visibles ; ne pas annoncer une plateforme fonctionnelle avant validation.

## API

POST /api/analyze avec {"url":"https://…"} : titre, plateforme, durée et URL de la page. Aucun lien média signé n’est exposé.
GET /api/download?url=… : fichier MP4 ou erreur JSON.

Limites : deux traitements simultanés, fichier de 300 Mo maximum, format MP4, pas de listes de lecture ni de diffusion en direct. Fichiers supprimés après transmission normale. Après un arrêt brutal du serveur, vider le stockage temporaire au redémarrage.

## Langues Blogger

Accueil en anglais ; créer/publier la page `/p/telecharger-video.html` pour la version française. Ces deux pages existent déjà sur videolinksave.blogspot.com.
