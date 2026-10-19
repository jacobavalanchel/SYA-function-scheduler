"""SCH design journeys: no model effects until confirmation; durable ownership."""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.core.config import Settings

main = importlib.import_module('app.main')
def plan():
    leaf = {'task_id':'leaf','title':'Read evidence','estimated_time':1.0,'children':[]}
    sub = {'task_id':'sub','title':'Gather sources','estimated_time':1.0,'children':[leaf]}
    stage = {'task_id':'stage','title':'Prepare','estimated_time':1.0,'children':[sub]}
    return {'task_id':'goal','title':'Research report','estimated_time':1.0,'children':[stage]}

class JourneyTests(unittest.TestCase):
    def test_preview_is_read_only_confirmation_and_preferences_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(main,'get_settings',return_value=Settings(scheduler_data_dir=folder,openai_api_key='')):
            with TestClient(main.app) as client:
                container=client.app.state.container
                container.llm_gateway.generate_task_tree=AsyncMock(return_value=plan())
                response=client.post('/api/scheduler/plans/preview',json={'input':'Research report'})
                self.assertEqual(response.status_code,200,response.text)
                tree=response.json()['data']['tree']
                self.assertEqual(client.get('/api/scheduler/tasks').json()['data'],[])
                self.assertFalse((Path(folder)/'planner.json').exists())
                self.assertEqual(client.post('/api/scheduler/plans/confirm',json={'tree':tree}).status_code,200)
                self.assertEqual(client.post('/api/scheduler/plans/confirm',json={'tree':tree}).status_code,409)
                leaf=tree['children'][0]['children'][0]['children'][0]['task_id']
                self.assertEqual(client.put('/api/scheduler/tasks/'+leaf,json={'scheduledDate':'2026-10-21','deadline':'2026-10-22','title':'Read updated evidence'}).status_code,200)
                result=client.put('/api/scheduler/config',json={'workStart':'10:00','workEnd':'16:00','dailyGranularity':'fine'})
                self.assertEqual(result.status_code,200)
                self.assertEqual(client.put('/api/scheduler/config',json={'workEnd':'08:00'}).status_code,422)
            with TestClient(main.app) as client:
                row=client.get('/api/scheduler/tasks/'+leaf).json()['data']
                self.assertEqual(row['title'],'Read updated evidence')
                self.assertEqual(row['scheduledDate'],'2026-10-21')
                self.assertEqual(row['deadline'],'2026-10-22')
                config=client.get('/api/scheduler/config').json()['data']
                self.assertEqual(config['workStart'],'10:00')
                self.assertEqual(config['workEnd'],'16:00')
                self.assertEqual(config['dailyGranularity'],'fine')

    def test_missing_model_returns_error_and_never_inserts_sample(self):
        with patch.object(main,'get_settings',return_value=Settings(openai_api_key='',scheduler_data_dir='')):
            with TestClient(main.app) as client:
                response=client.post('/api/scheduler/plans/preview',json={'input':'My goal'})
                self.assertEqual(response.status_code,422)
                self.assertEqual(client.get('/api/scheduler/tasks').json()['data'],[])

    def test_manual_plan_rejects_bad_depth_and_duplicate_ids(self):
        with TestClient(main.app) as client:
            shallow=plan()['children'][0]
            self.assertEqual(client.post('/api/scheduler/plans/confirm',json={'tree':shallow}).status_code,409)
            tree=plan();tree['children'][0]['task_id']='goal'
            self.assertEqual(client.post('/api/scheduler/plans/confirm',json={'tree':tree}).status_code,409)
            self.assertEqual(client.get('/api/scheduler/tasks').json()['data'],[])

    def test_disk_failure_rolls_back_edits_status_and_context(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(main,'get_settings',return_value=Settings(scheduler_data_dir=folder,openai_api_key='')):
            with TestClient(main.app) as client:
                self.assertEqual(client.post('/api/scheduler/plans/confirm',json={'tree':plan()}).status_code,200)
                store=client.app.state.container.memory_store
                before=client.get('/api/scheduler/tasks').json()['data']
                disk=(Path(folder)/'planner.json').read_text()
                with patch.object(store,'_persist',side_effect=OSError('synthetic disk failure')):
                    with self.assertRaises(OSError):
                        client.put('/api/scheduler/tasks/leaf',json={'title':'must not persist'})
                    with self.assertRaises(OSError):
                        client.portal.call(lambda: store.update_plan_context(task_id='goal',goal='changed',persona='balanced',deadline_days=3))
                    from app.models.task_tree import TaskStatus, TaskNode
                    with self.assertRaises(OSError):
                        client.portal.call(lambda: store.update_task_status(task_id='leaf',new_status=TaskStatus.DONE,actual_time=1.0))
                    with self.assertRaises(OSError):
                        client.portal.call(lambda: store.set_task_tree(TaskNode.from_llm_json(plan())))
                    failed_status=client.put('/api/scheduler/tasks/leaf',json={'status':'completed'})
                    self.assertEqual(failed_status.status_code,503)
                after=client.get('/api/scheduler/tasks').json()['data']
                for rows in (before,after):
                    for row in rows:row.pop('createdAt',None)
                self.assertEqual(after,before)
                self.assertEqual((Path(folder)/'planner.json').read_text(),disk)
                client.put('/api/scheduler/config',json={'dailyGranularity':'coarse'})
            with TestClient(main.app) as client:
                self.assertEqual(client.get('/api/scheduler/tasks/leaf').json()['data']['title'],'Read evidence')
                self.assertEqual(client.get('/api/scheduler/tasks/leaf').json()['data']['status'],'pending')
