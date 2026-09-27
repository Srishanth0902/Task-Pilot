"""56 concurrent user conversations, real graph/storage, simulated model/Calendar.

Does not access Google, use model credits, or deploy anything.
"""
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from statistics import median

from cryptography.fernet import Fernet
from app.api import ChatRequest
from app.multiuser import UserRuntime
from app.user_store import UserStore
from app.graph_agent import QueryPlan
from tests.test_advanced_agent import Planner,event
from tests.test_calendar_service import FakeService


class Model:
    def with_structured_output(self,*args,**kwargs):
        return Planner(QueryPlan(intent='list'))


def main():
    with tempfile.TemporaryDirectory() as folder:
        key=Fernet.generate_key()
        store=UserStore(folder,key)
        def service(uid):
            return FakeService(list_result={'items':[event(uid,uid+' personal meeting','2026-09-14T10:00:00+05:30','2026-09-14T11:00:00+05:30')]})
        runtime=UserRuntime(store,Model,service)
        gate=threading.Barrier(56)
        def run(index):
            uid=f'user-{index}'
            gate.wait(timeout=30)
            start=perf_counter()
            result=runtime.chat(uid,f'conversation-{index}','Please list my upcoming events')
            assert result['verified']
            assert result['tool_result']['events'][0]['event_id']==uid
            return perf_counter()-start
        start=perf_counter()
        with ThreadPoolExecutor(max_workers=56) as pool:
            times=list(pool.map(run,range(56)))
        fresh=UserStore(folder,key)
        for index in range(56):
            uid=f'user-{index}'
            assert len(fresh.conversations(uid))==1
            state,busy=fresh.conversation(uid,f'conversation-{index}')
            assert not busy and state['tool_result']['events'][0]['event_id']==uid
            try:
                fresh.conversation(uid,f'conversation-{(index+1)%56}')
            except PermissionError:
                pass
            else:
                raise AssertionError('Cross-account access allowed')
        print(json.dumps({'users':56,'passed':56,'elapsed_seconds':round(perf_counter()-start,3),
            'median_seconds':round(median(times),3),'p95_seconds':round(sorted(times)[53],3),
            'external_services':'simulated','persistence_and_isolation':'passed'},indent=2))


if __name__=='__main__':
    main()
