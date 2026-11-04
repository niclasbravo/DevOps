"""Pruebas del esquema: lo que la propia base debe impedir."""

import sqlite3
from contextlib import closing

import pytest

from app.db import ROLE_PERMISSIONS, connect, init_db


@pytest.fixture
def conn():
    init_db()
    with closing(connect()) as connection:
        yield connection


def _insert_audit(conn) -> None:
    conn.execute("INSERT INTO audit_log (ts, action, outcome) VALUES ('2026-11-04', 'prueba', 'permitido')")
    conn.commit()


def test_init_db_es_idempotente(conn):
    init_db()
    assert conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0] == 3


def test_permisos_cargados_coinciden_con_la_tabla_de_la_spec(conn):
    rows = conn.execute(
        """SELECT r.name, p.code FROM role_permissions rp
           JOIN roles r ON r.id = rp.role_id JOIN permissions p ON p.id = rp.permission_id"""
    ).fetchall()
    cargados: dict[str, set[str]] = {}
    for row in rows:
        cargados.setdefault(row["name"], set()).add(row["code"])
    assert cargados == ROLE_PERMISSIONS


def test_el_analista_no_tiene_permisos_de_cuentas_ni_auditoria():
    assert not {p for p in ROLE_PERMISSIONS["analista"] if p.startswith(("accounts:", "audit:"))}


def test_solo_el_usuario_puede_votar():
    assert [rol for rol, permisos in ROLE_PERMISSIONS.items() if "feedback:write" in permisos] == ["usuario"]


@pytest.mark.parametrize("sql", ["UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"])
def test_audit_log_solo_admite_inserciones(conn, sql):
    _insert_audit(conn)
    with pytest.raises(sqlite3.IntegrityError, match="solo inserción"):
        conn.execute(sql)


def test_voto_invalido_lo_rechaza_la_base(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO feedback (pseudonym, game, vote, voted_on) VALUES ('p', 'Hades', 5, '2026-11-04')"
        )


def test_un_solo_voto_por_seudonimo_y_juego(conn):
    sql = "INSERT INTO feedback (pseudonym, game, vote, voted_on) VALUES ('p', 'Hades', 1, '2026-11-04')"
    conn.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_feedback_no_tiene_clave_foranea(conn):
    assert conn.execute("PRAGMA foreign_key_list(feedback)").fetchall() == []


def test_alias_no_distingue_mayusculas_en_la_base(conn):
    sql = """INSERT INTO accounts (id, alias, password_hash, role_id, created_on)
             VALUES (?, ?, 'h', (SELECT id FROM roles WHERE name = 'usuario'), '2026-11-04')"""
    conn.execute(sql, ("1", "Nico"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("2", "nico"))


def test_rol_inexistente_se_rechaza(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO roles (name) VALUES ('superadmin')")


def test_connect_crea_el_directorio(monkeypatch, tmp_path):
    monkeypatch.setenv("GM_DB_PATH", str(tmp_path / "nuevo" / "gamematch.db"))
    init_db()
    assert (tmp_path / "nuevo" / "gamematch.db").exists()
