from chuan.main import run_cli
from chuan.scheduler import ProactiveScheduler


class FakeSupervisor:
    def __init__(self, **_: object) -> None:
        self.scheduler = ProactiveScheduler(lambda *_: {"messages": []})
        self.closed = False

    def wake_up(self) -> None:
        return None

    def shutdown(self) -> None:
        self.closed = True

    def dispatch(self, message: str):
        return {"messages": [{"role": "assistant", "content": f"收到：{message}"}]}

    def list_workers(self) -> list[str]:
        return ["housekeeper", "programmer"]


def test_cli_dispatches_message_and_shuts_down() -> None:
    answers = iter(["你好", "/workers", "exit"])
    output: list[str] = []
    created: list[FakeSupervisor] = []

    def factory(**kwargs: object) -> FakeSupervisor:
        supervisor = FakeSupervisor(**kwargs)
        created.append(supervisor)
        return supervisor

    run_cli(supervisor_factory=factory, input_fn=lambda _: next(answers), output_fn=output.append)

    assert any("收到：你好" in line for line in output)
    assert any("housekeeper" in line for line in output)
    assert created[0].closed is True
