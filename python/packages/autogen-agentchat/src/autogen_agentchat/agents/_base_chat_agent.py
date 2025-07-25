from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, List, Mapping, Sequence

from autogen_core import CancellationToken, ComponentBase, trace_create_agent_span, trace_invoke_agent_span
from pydantic import BaseModel

from ..base import ChatAgent, Response, TaskResult
from ..messages import (
    BaseAgentEvent,
    BaseChatMessage,
    ModelClientStreamingChunkEvent,
    TextMessage,
)
from ..state import BaseState


class BaseChatAgent(ChatAgent, ABC, ComponentBase[BaseModel]):
    """Base class for a chat agent.

    This abstract class provides a base implementation for a :class:`ChatAgent`.
    To create a new chat agent, subclass this class and implement the
    :meth:`on_messages`, :meth:`on_reset`, and :attr:`produced_message_types`.
    If streaming is required, also implement the :meth:`on_messages_stream` method.

    An agent is considered stateful and maintains its state between calls to
    the :meth:`on_messages` or :meth:`on_messages_stream` methods.
    The agent should store its state in the
    agent instance. The agent should also implement the :meth:`on_reset` method
    to reset the agent to its initialization state.

    .. note::

        The caller should only pass the new messages to the agent on each call
        to the :meth:`on_messages` or :meth:`on_messages_stream` method.
        Do not pass the entire conversation history to the agent on each call.
        This design principle must be followed when creating a new agent.
    """

    component_type = "agent"

    def __init__(self, name: str, description: str) -> None:
        """Initialize the agent with a name and description."""
        with trace_create_agent_span(
            agent_name=name,
            agent_description=description,
        ):
            self._name = name
            if self._name.isidentifier() is False:
                raise ValueError("The agent name must be a valid Python identifier.")
            self._description = description

    @property
    def name(self) -> str:
        """The name of the agent. This is used by team to uniquely identify
        the agent. It should be unique within the team."""
        return self._name

    @property
    def description(self) -> str:
        """The description of the agent. This is used by team to
        make decisions about which agents to use. The description should
        describe the agent's capabilities and how to interact with it."""
        return self._description

    @property
    @abstractmethod
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        """The types of messages that the agent produces in the
        :attr:`Response.chat_message` field. They must be :class:`BaseChatMessage` types."""
        ...

    @abstractmethod
    async def on_messages(self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken) -> Response:
        """Handles incoming messages and returns a response.

        .. note::

            Agents are stateful and the messages passed to this method should
            be the new messages since the last call to this method. The agent
            should maintain its state between calls to this method. For example,
            if the agent needs to remember the previous messages to respond to
            the current message, it should store the previous messages in the
            agent state.

        """
        ...

    async def on_messages_stream(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | Response, None]:
        """Handles incoming messages and returns a stream of messages and
        and the final item is the response. The base implementation in
        :class:`BaseChatAgent` simply calls :meth:`on_messages` and yields
        the messages in the response.

        .. note::

            Agents are stateful and the messages passed to this method should
            be the new messages since the last call to this method. The agent
            should maintain its state between calls to this method. For example,
            if the agent needs to remember the previous messages to respond to
            the current message, it should store the previous messages in the
            agent state.

        """
        response = await self.on_messages(messages, cancellation_token)
        for inner_message in response.inner_messages or []:
            yield inner_message
        yield response

    async def run(
        self,
        *,
        task: str | BaseChatMessage | Sequence[BaseChatMessage] | None = None,
        cancellation_token: CancellationToken | None = None,
        output_task_messages: bool = True,
    ) -> TaskResult:
        """Run the agent with the given task and return the result."""
        with trace_invoke_agent_span(
            agent_name=self.name,
            agent_description=self.description,
        ):
            if cancellation_token is None:
                cancellation_token = CancellationToken()
            input_messages: List[BaseChatMessage] = []
            output_messages: List[BaseAgentEvent | BaseChatMessage] = []
            if task is None:
                pass
            elif isinstance(task, str):
                text_msg = TextMessage(content=task, source="user")
                input_messages.append(text_msg)
                if output_task_messages:
                    output_messages.append(text_msg)
            elif isinstance(task, BaseChatMessage):
                input_messages.append(task)
                if output_task_messages:
                    output_messages.append(task)
            else:
                if not task:
                    raise ValueError("Task list cannot be empty.")
                # Task is a sequence of messages.
                for msg in task:
                    if isinstance(msg, BaseChatMessage):
                        input_messages.append(msg)
                        if output_task_messages:
                            output_messages.append(msg)
                    else:
                        raise ValueError(f"Invalid message type in sequence: {type(msg)}")
            response = await self.on_messages(input_messages, cancellation_token)
            if response.inner_messages is not None:
                output_messages += response.inner_messages
            output_messages.append(response.chat_message)
            return TaskResult(messages=output_messages)

    async def run_stream(
        self,
        *,
        task: str | BaseChatMessage | Sequence[BaseChatMessage] | None = None,
        cancellation_token: CancellationToken | None = None,
        output_task_messages: bool = True,
    ) -> AsyncGenerator[BaseAgentEvent | BaseChatMessage | TaskResult, None]:
        """Run the agent with the given task and return a stream of messages
        and the final task result as the last item in the stream.

        Args:
            task: The task to run. Can be a string, a single message, or a sequence of messages.
            cancellation_token: The cancellation token to kill the task immediately.
            output_task_messages: Whether to include task messages in the output stream. Defaults to True for backward compatibility.
        """
        with trace_invoke_agent_span(
            agent_name=self.name,
            agent_description=self.description,
        ):
            if cancellation_token is None:
                cancellation_token = CancellationToken()
            input_messages: List[BaseChatMessage] = []
            output_messages: List[BaseAgentEvent | BaseChatMessage] = []
            if task is None:
                pass
            elif isinstance(task, str):
                text_msg = TextMessage(content=task, source="user")
                input_messages.append(text_msg)
                if output_task_messages:
                    output_messages.append(text_msg)
                    yield text_msg
            elif isinstance(task, BaseChatMessage):
                input_messages.append(task)
                if output_task_messages:
                    output_messages.append(task)
                    yield task
            else:
                if not task:
                    raise ValueError("Task list cannot be empty.")
                for msg in task:
                    if isinstance(msg, BaseChatMessage):
                        input_messages.append(msg)
                        if output_task_messages:
                            output_messages.append(msg)
                            yield msg
                    else:
                        raise ValueError(f"Invalid message type in sequence: {type(msg)}")
            async for message in self.on_messages_stream(input_messages, cancellation_token):
                if isinstance(message, Response):
                    yield message.chat_message
                    output_messages.append(message.chat_message)
                    yield TaskResult(messages=output_messages)
                else:
                    yield message
                    if isinstance(message, ModelClientStreamingChunkEvent):
                        # Skip the model client streaming chunk events.
                        continue
                    output_messages.append(message)

    @abstractmethod
    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Resets the agent to its initialization state."""
        ...

    async def on_pause(self, cancellation_token: CancellationToken) -> None:
        """Called when the agent is paused while running in its :meth:`on_messages` or
        :meth:`on_messages_stream` method. This is a no-op by default in the
        :class:`BaseChatAgent` class. Subclasses can override this method to
        implement custom pause behavior."""
        pass

    async def on_resume(self, cancellation_token: CancellationToken) -> None:
        """Called when the agent is resumed from a pause while running in
        its :meth:`on_messages` or :meth:`on_messages_stream` method.
        This is a no-op by default in the :class:`BaseChatAgent` class.
        Subclasses can override this method to implement custom resume behavior."""
        pass

    async def save_state(self) -> Mapping[str, Any]:
        """Export state. Default implementation for stateless agents."""
        return BaseState().model_dump()

    async def load_state(self, state: Mapping[str, Any]) -> None:
        """Restore agent from saved state. Default implementation for stateless agents."""
        BaseState.model_validate(state)

    async def close(self) -> None:
        """Release any resources held by the agent. This is a no-op by default in the
        :class:`BaseChatAgent` class. Subclasses can override this method to
        implement custom close behavior."""
        pass
