-- R3 维护窗口：启用三个受限服务 LOGIN。
--
-- 密码只通过 psql 变量注入，禁止写入文件、命令输出或部署记录。调用示例只列变量名：
--   psql ... -v im_service_password -v sync_service_password -v python_backend_password \
--     -f backend/sql/hasn/operations/r3-enable-service-logins.sql
--
-- 三个角色及其对象权限必须已由 R2-11 正向迁移创建。本脚本只启用登录、收紧角色属性并授予
-- 当前数据库 CONNECT，不负责扩大 schema/table 权限。

\set ON_ERROR_STOP on

\if :{?im_service_password}
\else
  \echo '缺少 psql 变量 im_service_password'
  \quit 3
\endif
\if :{?sync_service_password}
\else
  \echo '缺少 psql 变量 sync_service_password'
  \quit 3
\endif
\if :{?python_backend_password}
\else
  \echo '缺少 psql 变量 python_backend_password'
  \quit 3
\endif

SELECT length(:'im_service_password') > 0 AS im_password_present,
       length(:'sync_service_password') > 0 AS sync_password_present,
       length(:'python_backend_password') > 0 AS python_password_present
\gset

\if :im_password_present
\else
  \echo 'im_service_password 不能为空'
  \quit 3
\endif
\if :sync_password_present
\else
  \echo 'sync_service_password 不能为空'
  \quit 3
\endif
\if :python_password_present
\else
  \echo 'python_backend_password 不能为空'
  \quit 3
\endif

BEGIN;

ALTER ROLE astra_im_service WITH
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'im_service_password';
ALTER ROLE astra_sync_service WITH
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'sync_service_password';
ALTER ROLE astra_python_backend WITH
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'python_backend_password';

SELECT format(
    'GRANT CONNECT ON DATABASE %I TO astra_im_service, astra_sync_service, astra_python_backend',
    current_database()
)
\gexec

COMMIT;
