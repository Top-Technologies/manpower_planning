def post_init_hook(env):
    env['approval.category']._ensure_manpower_categories()
