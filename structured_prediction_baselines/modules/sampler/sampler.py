from typing import List, Tuple, Union, Dict, Any, Optional
from allennlp.common.registrable import Registrable
import torch
from allennlp.common.lazy import Lazy
from structured_prediction_baselines.modules.score_nn import ScoreNN
from structured_prediction_baselines.modules.oracle_value_function import (
    OracleValueFunction,
)
import numpy as np


class Sampler(torch.nn.Module, Registrable):
    """
    Given input x, returns samples of shape `(batch, num_samples or 1,...)`
    and optionally their corresponding probabilities of shape `(batch, num_samples)`.
    **The sampler can do and return different things during training and test.**
    We want the probabilities specifically in the [[Minimum Risk Training for Neural Machine Translation|MRT setting]].

    The cases that sampler will cover include:
        1. Inference network or `TaskNN`, where we just take the input x and produce either a
            relaxed output of shape `(batch, 1, ...)` or samples of shape `(batch, num_samples, ...)`.
            Note, when we include `TaskNN` here, we also need to update its parameters, right here.
            So when sampler uses `TaskNN`, we also need to give it an instance of `Optimizer` to update its parameters.
        2. Cost-augmented inference module that uses `ScoreNN` and `OracleValueFunction` to produce a single relaxed output or samples.
        3. Adversarial sampler which again uses `ScoreNN` and `OracleValueFunction` to produce adversarial samples.
            (I see no difference between this and the cost augmented inference)
        4. Random samples biased towards `labels`.
        5. In the case of MRT style training, it can be beam search.
        6. In the case of vanilla feedforward model, one can just return the logits with shape `(batch, 1, ... )`
    """

    def __init__(
        self,
        score_nn: Optional[ScoreNN] = None,
        oracle_value_function: Optional[OracleValueFunction] = None,
        **kwargs: Any,
    ):
        super().__init__()  # type: ignore
        self.score_nn = score_nn
        self.oracle_value_function = oracle_value_function
        self._different_training_and_eval = False

    def forward(
        self, x: Any, labels: Any, **kwargs: Any
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """
        Returns:
            samples: Tensor of shape (batch, num_samples, ...)
            probabilities: None or tensor of shape (batch, num_samples)
        """
        raise NotImplementedError

    @property
    def different_training_and_eval(self) -> bool:
        return self._different_training_and_eval


@Sampler.register(
    "appending-container", constructor="from_partial_constituent_samplers"
)
class AppendingSamplerContainer(Sampler):
    """
    Appends the samples generated by different samplers into one single set of samples.

    This class is useful, for example, when you want each batch to have samples
    from ground truth, gradient based inference as well as adversarial.
    """

    def __init__(
        self,
        constituent_samplers: List[Sampler],
        score_nn: Optional[ScoreNN] = None,
        oracle_value_function: Optional[OracleValueFunction] = None,
    ):
        super().__init__(score_nn, oracle_value_function)
        self.constituent_samplers = constituent_samplers

    @classmethod
    def from_partial_constituent_samplers(
        cls,
        constituent_samplers: List[Lazy[Sampler]],
        score_nn: Optional[ScoreNN] = None,
        oracle_value_function: Optional[OracleValueFunction] = None,
    ) -> Sampler:
        constructed_samplers = [
            sampler.construct(
                score_nn=score_nn, oracle_value_function=oracle_value_function
            )
            for sampler in constituent_samplers
        ]

        return cls(
            constructed_samplers,
            score_nn=score_nn,
            oracle_value_function=oracle_value_function,
        )

    def forward(
        self, x: Any, labels: Any, **kwargs: Any
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        samples, probs = list(
            zip(
                *[
                    sampler(x, labels, **kwargs)
                    for sampler in self.constituent_samplers
                ]
            )
        )  # samples: List[Tensor(batch, num_samples_for_sampler, ...)],
        # probs: List[Tensor(batch, num_samples_for_sampler, ...) or None]

        # DP: Currently we will not support combining probs
        # We can do it later if we need.
        all_samples = torch.cat(samples, dim=1)

        return all_samples, None


@Sampler.register(
    "random-picking-container", constructor="from_partial_constituent_samplers"
)
class RandomPickingSamplerContainer(Sampler):
    """
    On each call, picks one sampler randomly and returns samples from that sampler.

    This class is useful, for example, when you want to have multiple sampling strategies
    but only want samples from one of them in every batch.
    """

    def __init__(
        self,
        constituent_samplers: List[Sampler],
        probabilities: Optional[List[float]] = None,
        score_nn: Optional[ScoreNN] = None,
        oracle_value_function: Optional[OracleValueFunction] = None,
    ):
        super().__init__(score_nn, oracle_value_function)
        self.constituent_samplers = torch.nn.ModuleList(constituent_samplers)

        if probabilities is not None:
            assert len(probabilities) == len(self.constituent_samplers)
            # normalize
            total = sum(probabilities)
            self.probabilities = [p / total for p in probabilities]
        else:  # None
            total = len(self.constituent_samplers)
            self.probabilities = [1.0 / total] * total

    @classmethod
    def from_partial_constituent_samplers(
        cls,
        constituent_samplers: List[Lazy[Sampler]],
        probabilities: Optional[List[float]] = None,
        score_nn: Optional[ScoreNN] = None,
        oracle_value_function: Optional[OracleValueFunction] = None,
    ) -> Sampler:
        constructed_samplers = [
            sampler.construct(
                score_nn=score_nn, oracle_value_function=oracle_value_function
            )
            for sampler in constituent_samplers
        ]

        return cls(
            constructed_samplers,
            probabilities=probabilities,
            score_nn=score_nn,
            oracle_value_function=oracle_value_function,
        )

    def forward(
        self, x: Any, labels: Any, **kwargs: Any
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:

        sampler = np.random.choice(
            self.constituent_samplers, p=self.probabilities
        )

        return sampler(x, labels, **kwargs)