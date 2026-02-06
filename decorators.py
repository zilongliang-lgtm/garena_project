import inspect
import typing
from functools import wraps

from django.core.cache import caches

from lib.settings import config


def cached_get(
    cache_key_base: str,
    expiry: int,
    key_params_index: typing.Tuple[int, ...] = tuple(),
    nullable=False,
    use: str = "default",
    use_cache: int = config.USE_CACHE,
):
    """
    Usage:
    @cached_get('some-key-prefix', 60)
    def my_cacheable_func(arg1, arg2, *, force_update=False):
        return 'hello world!'

    The function definition should have a `force_update` named variable.
    In Python 3, everything comes after "*," is named variable.

    Calling `my_cacheable_func` with `force_update` set to `True` would poke through cache intercepting,
    get the latest result, and thus update the cached value

    :param cache_key_base: cache key base
    :param expiry: in seconds
    :param key_params_index: indexes of args that will be appended to the key base to form the key; if [], use all the args
    """

    def generate_cache_key(base, key_params_index, *args):
        appends = list()
        if args:
            key_args = (
                [args[idx] for idx in key_params_index] if key_params_index else args
            )
            # All values in `args` must be string hashable
            for a in key_args:
                if inspect.isclass(a):
                    a = a.__name__
                appends.append(str(a))
        return f'{base}_{"_".join(appends)}' if appends else base

    def decorator(func):
        @wraps(func)
        def wrapper(*args, force_update=False):
            cache = caches[use]
            cache_key = generate_cache_key(
                cache_key_base + func.__qualname__, key_params_index, *args
            )
            result = None if force_update else cache.get(cache_key, None)
            if result is None:
                result = func(*args, **{"force_update": True} if force_update else {})
                if result or nullable:
                    cache.set(cache_key, result, expiry * use_cache)
            return result

        return wrapper

    return decorator
