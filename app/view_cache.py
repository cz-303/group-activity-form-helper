"""Small, UI-owned read cache. Keep only one search and one notice selection."""


class ViewCache:
    def __init__(self, store):
        self.store = store
        self.token = None
        self.values = {}

    def read(self, slot, argument, loader):
        # A monitor commit cannot occur between token inspection and the query.
        with self.store.lock:
            token = self.store.revision_token()
            if token != self.token:
                self.values.clear()
                self.token = token
            cached = self.values.get(slot)
            if cached is None or cached[0] != argument:
                value = loader()
                self.values[slot] = (argument, value)
            return self.values[slot][1]

    def rows(self):
        return self.read('rows', None, self.store.rows)

    def matching_urls(self, query):
        return self.read('search', query, lambda: self.store.matching_urls(query))

    def notices(self, url):
        return self.read('notices', url, lambda: self.store.notices(url))
