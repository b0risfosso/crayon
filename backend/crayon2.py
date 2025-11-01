from db_shared import (
    init_picture_db, init_usage_db,
    create_vision, create_picture, log_usage
)

# On startup
init_picture_db()
init_usage_db()