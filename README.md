GenericSQL
==========
This is a Sublime Text 2 plugin that runs SQL commands as an external shell process,
and shows the output in a new view. Also has basic explain-plan support.


License
=======
GPLv3, except for the exec_in_window code, which I stole from https://github.com/vhyza/exec-in-window


Install
=======
No Package Control magic here; just make directory `$HOME/.config/sublime-text-2/Packages/GenericSQL/` and copy these files into it.


Usage
=====
You need to make a new build system, and then edit you build file so it looks like this:

    {
        // selector only works when build system is Automatic
        // "selector": "source.sql",
        "target": "sql_exec",
        "cmd" : "",
        "variants": [
            // This first one is necessary - do not omit
            { "name": "Run", "cmd" : "reset" },
            { "name": "user@database",        "dialect": "oracle",   "cmd": ["sqlplus", "-s", "user/password@host.name:1521/database", "@"] },
            { "name": "user@database.host",   "dialect": "postgres", "cmd": ["psql", "host=host.name user=user password=password dbname=database", "-f"] },
            { "name": "user@database.host",   "dialect": "mysql",    "cmd": ["mysql", "-B", "-h", "host.name", "-P", "3306", "-u", "user", "-ppassword", "-D", "database", "-e source "] }
        ]
    }

To actually run an SQL file, press F7 or Ctrl-B (the normal key-binding to execute the build system).
The first time, you will be prompted to choose a database connection from the ones you defined in the build config.

The database you choose will be saved against the view, so the repeated executions of the same file
will hit the same database. If you want to choose a different database connection, press Shift-F7
or Shift-Ctrl-B.

F8 will run a snippet of SQL, rather than the entire file. It will highlight everything above and
below the current cursor position to the next blank line, copy it into a temp file,
and send it to whatever database connection has been saved against the view.
Shift-F8 is the same but forces a reselection of the database connection, like Shift-F7 does.

Ctrl-Shift-F8 does an explain-plan of the snippet.
