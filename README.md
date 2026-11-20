# Prisma – RGB Studio

Eine Windows-App, die **alle RGB-Geräte in einer Oberfläche** steuert: Gehäuse- und AIO-Lüfter am Mainboard,
Arbeitsspeicher, Tastatur, Maus und Zubehör. Prisma nutzt [OpenRGB](https://openrgb.org) als Hardware-Dienst
und bringt eine eigene Effekt-Engine, Szenen und einen Editor mit.

## Funktionen

- **37 Szenen** in sechs Kategorien (Gaming, Bunt, Natur, Entspannt, Musik, Praktisch) – ein Klick, weiche Überblendung
- **35 Effekte**, darunter Lüfter-Effekte wie Rotor, Doppel-Helix, Halo-Puls und Lüfter-Kaskade
- **Studio**: Szenen aus Ebenen bauen – Ziel pro Ebene (alle Geräte, alle Lüfter, Gerätetyp, Gerät, Zone),
  Farbwähler, 22 Paletten-Vorlagen, eigene Paletten
- **Reaktive Effekte**: Musik (Spektrum, Bass-Puls, Klangwelle) und CPU-/RAM-Auslastung
- **Geräte**: LED-Assistent für Lüfteranschlüsse, Lüfteraufteilung, Startpunkt, Richtung, Helligkeit inkl. Boost,
  Hardware-Effekte
- **Zeitplan** für automatische Szenenwechsel
- **60 FPS** mit Bewegungsglättung; Funkgeräte und RAM werden automatisch gedrosselt
- Infobereich-Symbol, Autostart, **automatische Updates** über GitHub-Releases

## Installation

Voraussetzungen: Windows 10/11, Python 3.12, [OpenRGB](https://openrgb.org/releases.html) (entpackt)

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Das Skript kopiert Prisma nach `%USERPROFILE%\.prisma\app`, richtet eine Python-Umgebung ein und legt
Verknüpfungen im Startmenü und auf dem Desktop an. Danach in Prisma unter **Einstellungen** den Pfad zu
`OpenRGB.exe` setzen. Für RAM- und Mainboard-RGB muss OpenRGB mit Adminrechten laufen.

## Aufbau

| Pfad | Inhalt |
|---|---|
| `app.py` | Start, Fenster (pywebview), Infobereich |
| `core/effects.py` | Effekt-Bibliothek |
| `core/engine.py` | Render-Schleife, Ebenen → Zonen, Nachbearbeitung |
| `core/devices.py` | OpenRGB-Anbindung und Demo-Geräte |
| `core/store.py` | Einstellungen, Szenen, Zeitplan (`~/.prisma/state.json`) |
| `core/updater.py` | Selbst-Update über GitHub-Releases |
| `ui/` | Oberfläche (HTML/CSS/JS) |

## Neue Version veröffentlichen

1. `VERSION` in `core/version.py` erhöhen
2. Committen, pushen und ein Release mit Tag `vX.Y.Z` anlegen – installierte Apps aktualisieren sich dann selbst.
