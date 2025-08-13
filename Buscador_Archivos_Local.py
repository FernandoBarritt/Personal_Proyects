"""
Buscador local de archivos (simple, en un solo archivo)
Uso básico:
  python buscador_local_archivos.py index /ruta/a/escanear
  python buscador_local_archivos.py search "mi archivo" --ext pdf --tag trabajo --min-size 1024 --after 2023-01-01
  python buscador_local_archivos.py tag-add /ruta/al/archivo "proyecto"
  python buscador_local_archivos.py tag-remove /ruta/al/archivo "proyecto"
  python buscador_local_archivos.py list-tags

Requisitos: Python 3.8+ (solo librerías estándar)
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

DB = Path.cwd() / "index_archivos.db"

# DB: archivos(id,path,name,ext,size,mtime)  tags(file_path, tag)

def db_connect():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS archivos (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE,
            name TEXT,
            ext TEXT,
            size INTEGER,
            mtime REAL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            file_path TEXT,
            tag TEXT,
            UNIQUE(file_path, tag)
        )
    """)
    conn.commit()
    return conn

# Indexar archivos recursivamente
def indexar(ruta, conn, verbose=False):
    ruta = Path(ruta)
    if not ruta.exists():
        print("Ruta no existe:", ruta)
        return
    cur = conn.cursor()
    contador = 0
    for root, dirs, files in os.walk(ruta):
        for f in files:
            p = Path(root) / f
            try:
                stat = p.stat()
            except Exception:
                continue
            nombre = p.name
            ext = p.suffix.lower().lstrip('.')
            size = stat.st_size
            mtime = stat.st_mtime
            cur.execute(
                "INSERT OR REPLACE INTO archivos (path, name, ext, size, mtime) VALUES (?, ?, ?, ?, ?)",
                (str(p), nombre, ext, size, mtime)
            )
            contador += 1
            if verbose and contador % 100 == 0:
                print(f"Indexados: {contador}")
    conn.commit()
    print(f"Indexación completada. Archivos indexados: {contador}")

# Añadir tag
def tag_add(path, tag, conn):
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO tags (file_path, tag) VALUES (?, ?)", (str(path), tag))
    conn.commit()
    print(f"Tag '{tag}' añadido a {path}")

# Eliminar tag
def tag_remove(path, tag, conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM tags WHERE file_path = ? AND tag = ?", (str(path), tag))
    conn.commit()
    print(f"Tag '{tag}' eliminado de {path}")

# Listar tags
def list_tags(conn):
    cur = conn.cursor()
    cur.execute("SELECT tag, COUNT(*) FROM tags GROUP BY tag ORDER BY COUNT(*) DESC")
    rows = cur.fetchall()
    if not rows:
        print("No hay tags.")
        return
    for tag, cnt in rows:
        print(f"{tag} ({cnt})")

# Buscar tags asociados a un archivo
def get_tags_for(path, conn):
    cur = conn.cursor()
    cur.execute("SELECT tag FROM tags WHERE file_path = ?", (str(path),))
    return [r[0] for r in cur.fetchall()]

# Filtro por condiciones
def aplica_filtros(row, args, conn):
    # row: (id, path, name, ext, size, mtime)
    idd, path, name, ext, size, mtime = row
    if args.ext and ext != args.ext.lstrip('.').lower():
        return False
    if args.tag:
        tags = get_tags_for(path, conn)
        if args.tag not in tags:
            return False
    if args.min_size and size < args.min_size:
        return False
    if args.max_size and size > args.max_size:
        return False
    if args.after:
        try:
            t = datetime.fromisoformat(args.after).timestamp()
        except Exception:
            t = 0
        if mtime < t:
            return False
    if args.before:
        try:
            t = datetime.fromisoformat(args.before).timestamp()
        except Exception:
            t = float('inf')
        if mtime > t:
            return False
    return True

# Score fuzzy
def score_nombre(query, name):
    return SequenceMatcher(None, query.lower(), name.lower()).ratio()

# Búsqueda
def buscar(query, args, conn):
    cur = conn.cursor()
    cur.execute("SELECT id, path, name, ext, size, mtime FROM archivos")
    rows = cur.fetchall()
    resultados = []
    for row in rows:
        if not aplica_filtros(row, args, conn):
            continue
        idd, path, name, ext, size, mtime = row
        sc_name = score_nombre(query, name)
        sc_path = score_nombre(query, path)
        # considerar tag exacto
        tags = get_tags_for(path, conn)
        sc_tag = 1.0 if query in tags else 0.0
        score = max(sc_name, sc_path, sc_tag)
        if score >= (args.threshold or 0.4):
            resultados.append((score, path, name, ext, size, mtime, tags))
    resultados.sort(key=lambda x: x[0], reverse=True)
    if not resultados:
        print("No se encontraron resultados.")
        return
    for sc, path, name, ext, size, mtime, tags in resultados[: args.limit]:
        fecha = datetime.fromtimestamp(mtime).isoformat(sep=' ', timespec='seconds')
        tb = ','.join(tags) if tags else '-'
        print(f"[{sc:.2f}] {name} | {ext} | {size} bytes | {fecha} | tags: {tb}\n  {path}")

# Mostrar info de un archivo
def show(path, conn):
    cur = conn.cursor()
    cur.execute("SELECT id, path, name, ext, size, mtime FROM archivos WHERE path = ?", (str(path),))
    row = cur.fetchone()
    if not row:
        print("Archivo no indexado.")
        return
    idd, path, name, ext, size, mtime = row
    tags = get_tags_for(path, conn)
    fecha = datetime.fromtimestamp(mtime).isoformat(sep=' ', timespec='seconds')
    print(f"Nombre: {name}\nRuta: {path}\nExt: {ext}\nTam: {size} bytes\nModificado: {fecha}\nTags: {tags}")

# CLI
def parse_args():
    p = argparse.ArgumentParser(description="Buscador local simple")
    sp = p.add_subparsers(dest='cmd')

    p_index = sp.add_parser('index')
    p_index.add_argument('path')
    p_index.add_argument('--verbose', action='store_true')

    p_tag_add = sp.add_parser('tag-add')
    p_tag_add.add_argument('path')
    p_tag_add.add_argument('tag')

    p_tag_rm = sp.add_parser('tag-remove')
    p_tag_rm.add_argument('path')
    p_tag_rm.add_argument('tag')

    p_list = sp.add_parser('list-tags')

    p_search = sp.add_parser('search')
    p_search.add_argument('query')
    p_search.add_argument('--ext')
    p_search.add_argument('--tag')
    p_search.add_argument('--min-size', type=int)
    p_search.add_argument('--max-size', type=int)
    p_search.add_argument('--after')
    p_search.add_argument('--before')
    p_search.add_argument('--limit', type=int, default=20)
    p_search.add_argument('--threshold', type=float, default=0.4)

    p_show = sp.add_parser('show')
    p_show.add_argument('path')

    return p.parse_args()


def main():
    args = parse_args()
    if not args.cmd:
        print("Usa: index, search, tag-add, tag-remove, list-tags, show")
        return
    conn = db_connect()
    if args.cmd == 'index':
        indexar(args.path, conn, args.verbose)
    elif args.cmd == 'tag-add':
        tag_add(args.path, args.tag, conn)
    elif args.cmd == 'tag-remove':
        tag_remove(args.path, args.tag, conn)
    elif args.cmd == 'list-tags':
        list_tags(conn)
    elif args.cmd == 'search':
        buscar(args.query, args, conn)
    elif args.cmd == 'show':
        show(args.path, conn)

if __name__ == '__main__':
    main()

import os
import difflib

# Funciones básicas del buscador
def indexar_archivos(directorio):
    archivos = []
    for carpeta, _, ficheros in os.walk(directorio):
        for fichero in ficheros:
            archivos.append(os.path.join(carpeta, fichero))
    return archivos

def buscar_archivos(archivos, termino):
    resultados = difflib.get_close_matches(termino, archivos, n=10, cutoff=0.3)
    return resultados

# Interfaz de menú
def menu():
    print("=== Buscador Local de Archivos ===")
    carpeta = input("Introduce la ruta de la carpeta a indexar: ")
    if not os.path.isdir(carpeta):
        print("Ruta no válida.")
        return

    archivos = indexar_archivos(carpeta)
    print(f"Se han indexado {len(archivos)} archivos.")

    while True:
        print("\n1. Buscar archivo")
        print("2. Salir")
        opcion = input("Selecciona una opción: ")

        if opcion == "1":
            termino = input("Introduce el término de búsqueda: ")
            resultados = buscar_archivos(archivos, termino)
            if resultados:
                print("\nCoincidencias encontradas:")
                for r in resultados:
                    print(r)
            else:
                print("No se encontraron coincidencias.")
        elif opcion == "2":
            print("Saliendo...")
            break
        else:
            print("Opción inválida.")

if __name__ == "__main__":
    menu()
