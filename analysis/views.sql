-- DuckDB 内存视图定义（阶段2启用）
-- JSONL 是事实源，DuckDB 只是架在文件上的查询引擎，不落盘、不进 git。
-- 用法: con.execute(open('analysis/views.sql', encoding='utf-8').read())

CREATE VIEW IF NOT EXISTS v_repo_daily AS
  SELECT * FROM read_json_auto('data/snapshots/*.jsonl');

-- 增速视图：d1 日增 / d7 周增 / d30 月增
CREATE VIEW IF NOT EXISTS v_delta AS
  SELECT full_name, date, stars,
         stars - lag(stars) OVER w     AS d1,
         stars - lag(stars, 7) OVER w  AS d7,
         stars - lag(stars, 30) OVER w AS d30
  FROM v_repo_daily
  WINDOW w AS (PARTITION BY repo_id ORDER BY date);
