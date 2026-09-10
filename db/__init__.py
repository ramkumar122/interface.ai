"""CoreDesk data layer package.

Modules:
  money       -- integer-cents <-> display-string conversion (no floats)
  connection  -- sqlite3 connection helper (row factory + foreign keys ON)
  schema.sql  -- DDL executed at init
  fixtures    -- seed data as Python literals
  queries     -- the only module that writes SQL (reads + writes + seed)
"""
