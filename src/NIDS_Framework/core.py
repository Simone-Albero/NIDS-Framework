import logging

import pandas as pd
from rich.logging import RichHandler
import torch
from torch import nn, optim
from torch.utils.data import DataLoader
from sklearn.model_selection import train_test_split

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

def standard_pipeline(WINDOW_SIZE):
    CONFIG_PATH = "configs/dataset_properties.ini"
    DATASET_NAME = "ugr16_custom"
    named_prop = properties.NamedDatasetProperties(CONFIG_PATH)
    prop = named_prop.get_properties(DATASET_NAME)

    df_path = "dataset/UGR16/ms_1.csv"
    df = pd.read_csv(df_path)
    df_train, df_test = train_test_split(df, test_size=0.3, random_state=42)

    trans_builder = transformation_builder.TransformationBuilder()
    CATEGORICAL_LEV = 32
    BOUND = 1_000_000_000

    min_values, max_values = utilities.min_max_values(df_train, prop, BOUND)
    unique_values = utilities.unique_values(df_train, prop, CATEGORICAL_LEV)

    @trans_builder.add_step(order=1)
    def base_pre_processing(dataset, properties):
        utilities.base_pre_processing(dataset, properties, BOUND)

    @trans_builder.add_step(order=2)
    def log_pre_processing(dataset, properties):
        utilities.log_pre_processing(dataset, properties, min_values, max_values)

    @trans_builder.add_step(order=3)
    def categorical_conversion(dataset, properties):
        utilities.categorical_pre_processing(
            dataset, properties, unique_values, CATEGORICAL_LEV
        )

    @trans_builder.add_step(order=4)
    def bynary_label_conversion(dataset, properties):
        utilities.bynary_label_conversion(dataset, properties)

    transformations = trans_builder.build()

    proc = processor.Processor(df_train, prop)
    proc.transformations = transformations
    proc.apply()
    X_train, y_train = proc.build()

    proc = processor.Processor(df_test, prop)
    proc.transformations = transformations
    proc.apply()
    X_test, y_test = proc.build()


    device = "cpu"
    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"

    train_dataset = tabular_datasets.TabularDataset(
        X_train[prop.numeric_features],
        X_train[prop.categorical_features],
        device,
        y_train
    )

    test_dataset = tabular_datasets.TabularDataset(
        X_test[prop.numeric_features], 
        X_test[prop.categorical_features], 
        device, 
        y_test
    )

    @trans_builder.add_step(order=1)
    def categorical_one_hot(sample, categorical_levels=CATEGORICAL_LEV):
        return utilities.one_hot_encoding(sample, categorical_levels)

    transformations = trans_builder.build()
    train_dataset.set_categorical_transformation(transformations)
    test_dataset.set_categorical_transformation(transformations)

    BATCH_SIZE = 64
    #WINDOW_SIZE = 8
    EMBED_DIM = 128
    NUM_HEADS = 2
    NUM_LAYERS = 2
    DROPUT = 0.1
    DIM_FF = 64
    LR = 0.0005
    WHIGHT_DECAY = 0.001

    train_sampler = samplers.RandomSlidingWindowSampler(
        train_dataset, window_size=WINDOW_SIZE
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

    N_EPOCH = 1
    EPOCH_STEPS = 6000
    EPOCH_UNTIL_VALIDATION = 100
    PATIENCE = 2
    DELTA = 0.01

    train = trainer.Trainer(model, criterion, optimizer, device)
    train.train(
        n_epoch=N_EPOCH,
        train_data_loader=train_dataloader,
        epoch_steps=EPOCH_STEPS,
    )
    train.save_model(f"saves/s{WINDOW_SIZE}.pt")

    metric = metrics.BinaryClassificationMetric()
    train.test(test_dataloader, metric)


def fixed_pipeline(WINDOW_SIZE):
    CONFIG_PATH = "configs/dataset_properties.ini"
    DATASET_NAME = "ugr16_custom"
    named_prop = properties.NamedDatasetProperties(CONFIG_PATH)
    prop = named_prop.get_properties(DATASET_NAME)

    # train_path = "dataset/UGR16/ms_1.csv"
    # df = pd.read_csv(train_path)
    #df_train, df_test = train_test_split(df, test_size=0.3, random_state=42)
    # df_train['te'] = pd.to_datetime(df_train['te'])
    # df_train = df_train.sort_values(by='te')
    # df_test['te'] = pd.to_datetime(df_test['te'])
    # df_test = df_test.sort_values(by='te')

    # df_train.to_csv("dataset/UGR16/custom/ms_1_train.csv", index=False)
    # df_test.to_csv("dataset/UGR16/custom/ms_1_test.csv", index=False)
    # exit(1)
    
    train_path = "dataset/UGR16/custom/ms_1_train.csv"
    df_train = pd.read_csv(train_path)

    test_path = "dataset/UGR16/custom/ms_1_test.csv"
    df_test = pd.read_csv(test_path)

    trans_builder = transformation_builder.TransformationBuilder()
    CATEGORICAL_LEV = 32
    BOUND = 1_000_000_000

    min_values, max_values = utilities.min_max_values(df_train, prop, BOUND)
    unique_values = utilities.unique_values(df_train, prop, CATEGORICAL_LEV)

    @trans_builder.add_step(order=1)
    def base_pre_processing(dataset, properties):
        utilities.base_pre_processing(dataset, properties, BOUND)

    @trans_builder.add_step(order=2)
    def log_pre_processing(dataset, properties):
        utilities.log_pre_processing(dataset, properties, min_values, max_values)

    @trans_builder.add_step(order=3)
    def categorical_conversion(dataset, properties):
        utilities.categorical_pre_processing(
            dataset, properties, unique_values, CATEGORICAL_LEV
        )

    @trans_builder.add_step(order=4)
    def bynary_label_conversion(dataset, properties):
        utilities.bynary_label_conversion(dataset, properties)

    transformations = trans_builder.build()

    proc = processor.Processor(df_train, prop)
    proc.transformations = transformations
    proc.apply()
    X_train, y_train = proc.build()

    proc = processor.Processor(df_test, prop)
    proc.transformations = transformations
    proc.apply()
    X_test, y_test = proc.build()


    device = "cpu"
    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"

    train_dataset = tabular_datasets.TabularDataset(
        X_train[prop.numeric_features],
        X_train[prop.categorical_features],
        device,
        y_train
    )

    test_dataset = tabular_datasets.TabularDataset(
        X_test[prop.numeric_features], 
        X_test[prop.categorical_features], 
        device, 
        y_test
    )

    @trans_builder.add_step(order=1)
    def categorical_one_hot(sample, categorical_levels=CATEGORICAL_LEV):
        return utilities.one_hot_encoding(sample, categorical_levels)

    transformations = trans_builder.build()
    train_dataset.set_categorical_transformation(transformations)
    test_dataset.set_categorical_transformation(transformations)

    BATCH_SIZE = 64
    #WINDOW_SIZE = 8
    EMBED_DIM = 128
    NUM_HEADS = 2
    NUM_LAYERS = 2
    DROPUT = 0.1
    DIM_FF = 64
    LR = 0.0005
    WHIGHT_DECAY = 0.001

    train_indices = pd.read_csv(f"dataset/UGR16/fixed/ms_1_train_{WINDOW_SIZE}.csv")["index"]
    train_sampler = samplers.FixedWindowSampler(
        train_dataset, train_indices, window_size=WINDOW_SIZE
    )
    test_indices = pd.read_csv(f"dataset/UGR16/fixed/ms_1_test_{WINDOW_SIZE}.csv")["index"]
    test_sampler = samplers.FixedWindowSampler(
        test_dataset, test_indices, window_size=WINDOW_SIZE
    )

    train_dataloader = DataLoader(
        train_dataset,
        batch_size=BATCH_SIZE,
        sampler=train_sampler,
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

    N_EPOCH = 1
    EPOCH_STEPS = 6000
    EPOCH_UNTIL_VALIDATION = 100
    PATIENCE = 2
    DELTA = 0.01

    train = trainer.Trainer(model, criterion, optimizer, device)
    train.train(
        n_epoch=N_EPOCH,
        train_data_loader=train_dataloader,
        epoch_steps=EPOCH_STEPS,
    )
    train.save_model(f"saves/f{WINDOW_SIZE}.pt")

    metric = metrics.BinaryClassificationMetric()
    train.test(test_dataloader, metric)


def main():
    debug_level = logging.INFO
    logging.basicConfig(
        level=debug_level,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_time=False, show_path=False)],
    )
    
    for window_size in [2,4,8,16,32,64]:
        fixed_pipeline(window_size)


if __name__ == "__main__":
    main()
