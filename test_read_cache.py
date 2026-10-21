import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from read_cache import ReadCache


class ReadCacheTests(unittest.TestCase):
    def test_ttl_begins_after_read_and_copies_preserve_observation_timestamp(self):
        now=[0];cache=ReadCache(clock=lambda:now[0]);calls=[]
        def read():
            calls.append(1);now[0]+=2
            return {'checked_at':123,'items':[]}
        first=cache.get('one',read,1);first['items'].append('changed')
        self.assertEqual(cache.get('one',read,1),{'checked_at':123,'items':[]})
        self.assertEqual(len(calls),1)
        now[0]=3
        cache.get('one',read,1);self.assertEqual(len(calls),2)

    def test_simultaneous_readers_share_one_observation(self):
        cache=ReadCache();entered=threading.Event();release=threading.Event();calls=[]
        def read():
            calls.append(1);entered.set();self.assertTrue(release.wait(3));return {'value':[1]}
        with ThreadPoolExecutor(max_workers=8) as pool:
            first=pool.submit(cache.get,'same',read,10)
            self.assertTrue(entered.wait(3))
            others=[pool.submit(cache.get,'same',read,10) for _ in range(7)]
            release.set()
            results=[f.result(3) for f in [first,*others]]
        self.assertEqual(len(calls),1)
        results[0]['value'].append(2)
        self.assertEqual(results[1],{'value':[1]})

    def test_failure_is_not_cached_and_does_not_serve_expired_value(self):
        now=[0];cache=ReadCache(clock=lambda:now[0])
        cache.get('one',lambda:'old',1);now[0]=2
        def fail():raise OSError('offline')
        with self.assertRaises(OSError):cache.get('one',fail,1)
        self.assertEqual(cache.get('one',lambda:'new',1),'new')

    def test_invalidation_fences_inflight_results_and_new_reads(self):
        cache=ReadCache();entered=threading.Event();release=threading.Event()
        def old():entered.set();release.wait(3);return 'before mutation'
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(cache.get,'one',old,10)
            self.assertTrue(entered.wait(3));cache.clear()
            self.assertEqual(cache.get('one',lambda:'after mutation',10),'after mutation')
            release.set();self.assertEqual(first.result(3),'before mutation')
        self.assertEqual(cache.get('one',lambda:'unexpected',10),'after mutation')

    def test_cache_is_bounded_and_least_recently_used(self):
        cache=ReadCache(capacity=2)
        for name in ('one','two','one','three'):cache.get(name,lambda:name,10)
        self.assertEqual(list(cache.values),['one','three'])


if __name__=='__main__':unittest.main()
