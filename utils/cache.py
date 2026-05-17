"""
Cache utility for storing frequently accessed data.
"""

import os
import json
import time
import pickle
from pathlib import Path

from config.settings import CACHE_DIR, CACHE_ENABLED, CACHE_EXPIRY


class Cache:
    """
    A simple file-based cache implementation.
    """
    
    def __init__(self, namespace="default", expiry=CACHE_EXPIRY):
        """
        Initialize a cache instance.
        
        Args:
            namespace (str): Namespace for this cache to avoid key conflicts
            expiry (int): Default cache expiry time in seconds
        """
        self.namespace = namespace
        self.cache_dir = os.path.join(CACHE_DIR, namespace)
        self.enabled = CACHE_ENABLED
        self.default_expiry = expiry
        
        # Create cache directory if it doesn't exist
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)
    
    def _get_cache_path(self, key):
        """
        Get the file path for a cache key.
        
        Args:
            key (str): Cache key
            
        Returns:
            str: File path for the cache item
        """
        # Generate a safe filename from the key
        safe_key = "".join(c if c.isalnum() else "_" for c in str(key))
        return os.path.join(self.cache_dir, f"{safe_key}.cache")
    
    def set(self, key, value, ttl=None):
        """
        Store a value in the cache.
        
        Args:
            key (str): Cache key
            value: Value to store
            ttl (int): Time to live in seconds
        """
        if not self.enabled:
            return
        
        if ttl is None:
            ttl = self.default_expiry
            
        cache_path = self._get_cache_path(key)
        
        # Create cache entry with expiration time
        cache_data = {
            'expires_at': time.time() + ttl,
            'data': value
        }
        
        # Pickle the data to handle complex objects. Write to a
        # process-unique temp file in the same dir, then atomically
        # os.replace() it into place: a concurrent reader either sees
        # the old complete entry or the new complete entry, never a
        # half-written file (KI-5 step 3). os.replace is atomic on
        # both POSIX and Windows. A cache write must never crash the
        # caller, so failures are swallowed best-effort.
        tmp_path = f"{cache_path}.{os.getpid()}.tmp"
        try:
            with open(tmp_path, 'wb') as f:
                pickle.dump(cache_data, f)
            os.replace(tmp_path, cache_path)
        except OSError:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    
    def get(self, key):
        """
        Retrieve a value from the cache.
        
        Args:
            key (str): Cache key
            
        Returns:
            The cached value or None if not found or expired
        """
        if not self.enabled:
            return None
        
        cache_path = self._get_cache_path(key)
        
        # Check if cache file exists
        if not os.path.exists(cache_path):
            return None
        
        try:
            # Load cache entry.
            with open(cache_path, 'rb') as f:
                cache_data = pickle.load(f)
        except OSError:
            # Locked / unreadable (a concurrent writer holds it open,
            # Windows share violation, perms). The other process owns
            # the file — treat as a cache MISS and do NOT attempt
            # eviction in the read path (KI-5 step 2).
            return None
        except (pickle.PickleError, EOFError):
            # Genuinely corrupt content (distinct from a lock). Safe to
            # evict, but tolerate a concurrent eviction racing us so a
            # normal race never crashes the caller (KI-5 step 1).
            try:
                os.remove(cache_path)
            except OSError:
                pass
            return None

        # Check if cache entry has expired. Eviction is deferred to the
        # cleanup() maintenance sweep rather than unlinking in the hot
        # path — a stale entry is harmless until swept (it reads as a
        # miss and the next set() atomically replaces it), and keeping
        # the read path unlink-free removes the cross-process race
        # entirely (KI-5 step 3).
        if time.time() > cache_data['expires_at']:
            return None

        return cache_data['data']
    
    def delete(self, key):
        """
        Delete a value from the cache.
        
        Args:
            key (str): Cache key
        """
        if not self.enabled:
            return
        
        cache_path = self._get_cache_path(key)
        
        # Remove cache file if it exists
        if os.path.exists(cache_path):
            os.remove(cache_path)
    
    def clear(self):
        """
        Clear all items in this cache namespace.
        """
        if not self.enabled or not os.path.exists(self.cache_dir):
            return
        
        # Remove all cache files in the namespace directory
        for cache_file in os.listdir(self.cache_dir):
            file_path = os.path.join(self.cache_dir, cache_file)
            if os.path.isfile(file_path):
                os.remove(file_path)
    
    def cleanup(self):
        """
        Remove all expired cache items.

        This is the deferred-eviction maintenance sweep referenced by
        get() (KI-5 step 3): the read path no longer unlinks, so call
        this periodically to reclaim expired/corrupt entries. Every
        unlink is race-tolerant — a file locked by a concurrent reader
        is skipped this pass, not crashed on.
        """
        if not self.enabled or not os.path.exists(self.cache_dir):
            return

        current_time = time.time()

        # Check all cache files in the namespace directory
        for cache_file in os.listdir(self.cache_dir):
            file_path = os.path.join(self.cache_dir, cache_file)

            if not os.path.isfile(file_path):
                continue

            try:
                with open(file_path, 'rb') as f:
                    cache_data = pickle.load(f)
                expired = current_time > cache_data['expires_at']
            except (pickle.PickleError, EOFError):
                expired = True            # corrupt -> reclaim
            except OSError:
                continue                  # locked/unreadable -> next sweep

            if expired:
                try:
                    os.remove(file_path)
                except OSError:
                    pass