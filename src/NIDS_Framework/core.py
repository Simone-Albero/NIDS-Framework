import logging

import pandas as pd
from rich.logging import RichHandler
import torch
from torch import nn, optim
from torch.utils.data import DataLoader

from data import (
    properties,
    processor,
    utilities,
    transformation_builder,
    samplers,
    tabular_datasets,
)
from model import (
    nn_classifier,
    input_encoder,
    transformer,
    classification_head,
)
from training import (
    trainer,
    metrics,
)

def standard_pipeline():
    CONFIG_PATH = "configs/dataset_properties.ini"
    DATASET_NAME = "ugr16_custom"
    named_prop = properties.NamedDatasetProperties(CONFIG_PATH)
    prop = named_prop.get_properties(DATASET_NAME)

    dataset_path = "dataset/UGR16/custom/ms_train.csv"
    df = pd.read_csv(dataset_path)

    proc = processor.Processor(df, prop)

    trans_builder = transformation_builder.TransformationBuilder()
    CATEGORICAL_LEV = 32
    BOUND = 1_000_000_000

    @trans_builder.add_step(order=1)
    def base_pre_processing(dataset, properties, train_mask):
        utilities.base_pre_processing(dataset, properties, train_mask, BOUND)

    @trans_builder.add_step(order=2)
    def log_pre_processing(dataset, properties, train_mask):
        utilities.log_pre_processing(dataset, properties, train_mask)

    @trans_builder.add_step(order=3)
    def categorical_conversion(
        dataset, properties, train_mask, categorical_levels=CATEGORICAL_LEV
    ):
        utilities.categorical_pre_processing(
            dataset, properties, train_mask, categorical_levels
        )

    @trans_builder.add_step(order=4)
    def bynary_label_conversion(dataset, properties, train_mask):
        utilities.bynary_label_conversion(dataset, properties, train_mask)

    proc.transformations = trans_builder.build()
    proc.apply()

    X_train, y_train = proc.get_train()
    X_vaild, y_valid = proc.get_valid()
    X_test, y_test = proc.get_test()

    device = "cpu"
    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"

    train_dataset = tabular_datasets.TabularDataset(
        X_train[prop.numeric_features],
        X_train[prop.categorical_features],
        device,
        y_train,
    )
    valid_dataset = tabular_datasets.TabularDataset(
        X_vaild[prop.numeric_features],
        X_vaild[prop.categorical_features],
        device,
        y_valid,
    )
    test_dataset = tabular_datasets.TabularDataset(
        X_test[prop.numeric_features], X_test[prop.categorical_features], device, y_test
    )

    @trans_builder.add_step(order=1)
    def categorical_one_hot(sample, categorical_levels=CATEGORICAL_LEV):
        return utilities.one_hot_encoding(sample, categorical_levels)

    transformations = trans_builder.build()
    train_dataset.set_categorical_transformation(transformations)
    valid_dataset.set_categorical_transformation(transformations)
    test_dataset.set_categorical_transformation(transformations)

    BATCH_SIZE = 64
    WINDOW_SIZE = 2
    EMBED_DIM = 256
    NUM_HEADS = 2
    NUM_LAYERS = 4
    DROPUT = 0.1
    DIM_FF = 128
    LR = 0.0005
    WHIGHT_DECAY = 0.001

    train_sampler = samplers.RandomSlidingWindowSampler(
        train_dataset, window_size=WINDOW_SIZE
    )
    valid_sampler = samplers.RandomSlidingWindowSampler(
        valid_dataset, window_size=WINDOW_SIZE
    )
    test_sampler = samplers.RandomSlidingWindowSampler(
        test_dataset, window_size=WINDOW_SIZE
    )

    train_dataloader = DataLoader(
        train_dataset,
        batch_size=BATCH_SIZE,
        sampler=train_sampler,
        drop_last=True,
        shuffle=False,
    )
    valid_dataloader = DataLoader(
        valid_dataset,
        batch_size=BATCH_SIZE,
        sampler=valid_sampler,
        drop_last=True,
        shuffle=False,
    )
    test_dataloader = DataLoader(
        test_dataset,
        batch_size=BATCH_SIZE,
        sampler=test_sampler,
        drop_last=True,
        shuffle=False,
    )

    inputs, _ = next(iter(train_dataloader))
    input_shape = inputs.shape[-1]

    input_encoding = input_encoder.InputEncoder(input_shape, EMBED_DIM)
    transformer_block = transformer.TransformerEncoderOnly(
        EMBED_DIM, NUM_HEADS, NUM_LAYERS, DIM_FF, DROPUT
    )
    class_head = classification_head.ClassificationHead(EMBED_DIM, 1)

    model = nn_classifier.NNClassifier(
        input_encoding, transformer_block, class_head
    ).to(device=device)

    total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    logging.info(f"Total number of parameters: {total_params}")

    class_proportions = y_train.value_counts(normalize=True).sort_index()
    weights = 1.0 / class_proportions.values
    weights = weights / weights.sum()
    logging.info(f"weights: {weights}")

    criterion = nn.BCELoss(weight=torch.tensor(weights, dtype=torch.float32, device=device)[1])
    optimizer = optim.Adam(
        model.parameters(),
        lr=LR,
        weight_decay=WHIGHT_DECAY,
    )

    N_EPOCH = 4
    EPOCH_STEPS = 1000
    EPOCH_UNTIL_VALIDATION = 100
    PATIENCE = 2
    DELTA = 0.01

    train = trainer.Trainer(model, criterion, optimizer, device)
    # train.load_model()

    train.train(
        n_epoch=N_EPOCH,
        train_data_loader=train_dataloader,
        epoch_steps=EPOCH_STEPS,
        epochs_until_validation=EPOCH_UNTIL_VALIDATION,
        valid_data_loader=valid_dataloader,
        patience=PATIENCE,
        delta=DELTA,
    )

    metric = metrics.BinaryClassificationMetric()

    train.test(test_dataloader, metric)
    train.save_model()


def main():
    debug_level = logging.INFO
    logging.basicConfig(
        level=debug_level,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_time=False, show_path=False)],
    )
    standard_pipeline()


if __name__ == "__main__":
    main()
